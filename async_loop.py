# Adapted from the Blender Cloud Addon
# https://github.com/dfelinto/blender-cloud-addon/

import math
import sys
import asyncio
import concurrent.futures
import gc
import logging
import traceback
import bpy
from bpy import context

log = logging.getLogger(__name__)

# Keeps track of whether a loop-kicking operator is already running.
_loop_kicking_operator_running = False

def setup_asyncio_executor():
  """Sets up AsyncIO to run on a single thread."""

  executor = concurrent.futures.ThreadPoolExecutor()
  loop = asyncio.get_event_loop()
  loop.set_default_executor(executor)

def ensure_async_loop():
  log.debug('Starting asyncio loop')
  result = bpy.ops.asyncio.loop()
  log.debug(f'Result of starting modal operator is {result}')

def kick_async_loop(*args) -> bool:
  """Performs a single iteration of the asyncio event loop.
  :return: whether the asyncio loop should stop after this kick.
  """

  loop = asyncio.get_event_loop()

  # Even when we want to stop, we always need to do one more
  # 'kick' to handle task-done callbacks.
  stop_after_this_kick = False

  if loop.is_closed():
    log.warning('Loop closed, stopping immediately.')
    return True

  all_tasks = asyncio.Task.all_tasks()

  if len(all_tasks) == 0:
    log.debug('No more scheduled tasks, stopping after this kick.')
    stop_after_this_kick = True
  elif all(task.done() for task in all_tasks):
    # All tasks are done, so clean up and log results

    log.debug(f'All {len(all_tasks)} tasks are done, fetching results and stopping after this kick.')
    stop_after_this_kick = True

    # Clean up circular references between tasks.
    gc.collect()

    for task_idx, task in enumerate(all_tasks):
      if not task.done():
        continue

      try:
        res = task.result()
        log.debug(f'   task #{task_idx}: result={res}')
      except asyncio.CancelledError:
        # No problem, we want to stop anyway.
        log.debug(f'   task #{task_idx}: cancelled')
      except Exception:
        print(f'{task}: resulted in exception')
        traceback.print_exc()

  # If stop() is called before run_forever() is called,
  # the loop will poll the I/O selector once with a timeout of zero,
  # run all callbacks scheduled in response to I/O events
  # (and those that were already scheduled), and then exit.
  loop.stop()
  loop.run_forever()

  return stop_after_this_kick

class AsyncLoopModalOperator(bpy.types.Operator):
  bl_idname = 'asyncio.loop'
  bl_label = 'Runs the asyncio main loop'

  update_period = 1/15
  timer = None
  log = logging.getLogger(f'{__name__}.AsyncLoopModalOperator')

  def __del__(self):
    global _loop_kicking_operator_running

    # This can be required when the operator is running while Blender
    # (re)loads a file. The operator then doesn't get the chance to
    # finish the async tasks, hence stop_after_this_kick is never True.
    _loop_kicking_operator_running = False

  def execute(self, context):
    return self.invoke(context, None)

  def invoke(self, context, event):
    global _loop_kicking_operator_running

    if _loop_kicking_operator_running:
      self.log.debug('Another loop-kicking operator is already running.')
      return {'PASS_THROUGH'}

    context.window_manager.modal_handler_add(self)
    _loop_kicking_operator_running = True

    wm = context.window_manager
    self.timer = wm.event_timer_add(self.update_period, window=context.window)

    return {'RUNNING_MODAL'}

  def modal(self, context, event):
    global _loop_kicking_operator_running

    # If _loop_kicking_operator_running is set to False, someone called
    # erase_async_loop(). This is a signal that we really should stop
    # running.
    if not _loop_kicking_operator_running:
      return {'FINISHED'}

    if event.type != 'TIMER':
      return {'PASS_THROUGH'}

    stop_after_this_kick = kick_async_loop()

    if stop_after_this_kick:
      context.window_manager.event_timer_remove(self.timer)
      _loop_kicking_operator_running = False

      self.log.debug('Stopped asyncio loop kicking')
      return {'FINISHED'}

    return {'RUNNING_MODAL'}

class AsyncModalOperatorMixin:
  log = logging.getLogger(f'{__name__}.AsyncModalOperatorMixin')

  async_task = None
  signalling_future = None  # asyncio future for signalling that we want to cancel everything.
  update_period = 1 / 15

  _state = 'INITIALIZING'

  # Whether to keep running when the async task encounters an exception
  stop_upon_exception = False

  def invoke(self, context, event):
    context.window_manager.modal_handler_add(self)
    self.timer = context.window_manager.event_timer_add(self.update_period, window=context.window)

    self.log.info(f'Starting. Will run at {1 / self.update_period} Hz')
    self._new_async_task(self.async_execute(context))

    return {'RUNNING_MODAL'}

  async def async_execute(self, context):
    """Entry point of the asynchronous operator.
    Implement in a subclass.
    """
    return

  def quit(self):
    """Signals the Blender state machine to stop this operator from running."""
    self._state = 'QUIT'

  def execute(self, context):
    return self.invoke(context, None)

  def modal(self, context, event):
    task = self.async_task

    if self._state != 'EXCEPTION' and task and task.done() and not task.cancelled():
      ex = task.exception()

      if ex is not None:
        self._state = 'EXCEPTION'
        self.log.error(f'Exception while running task: {ex}')

        if self.stop_upon_exception:
          self.quit()
          self._finish(context)
          return {'FINISHED'}

        return {'RUNNING_MODAL'}

    if self._state == 'QUIT':
      self._finish(context)
      return {'FINISHED'}

    return {'PASS_THROUGH'}

  def _finish(self, context):
    self._stop_async_task()
    context.window_manager.event_timer_remove(self.timer)

  def _new_async_task(self, async_task: asyncio.coroutine, future: asyncio.Future = None):
    """Stops the currently running async task, and starts another one."""

    self.log.debug(f'Setting up a new task {async_task}, so any existing task must be stopped')
    self._stop_async_task()

    self.signalling_future = future or asyncio.Future()
    self.async_task = asyncio.ensure_future(async_task)
    self.log.debug(f'Created new task {self.async_task}')

    # Start the async manager so everything happens
    ensure_async_loop()

  def _stop_async_task(self):
    self.log.debug('Stopping async task')

    if self.async_task is None:
      self.log.debug('No async task, trivially stopped')
      return

    # Signal that we want to stop

    self.async_task.cancel()

    if not self.signalling_future.done():
      self.log.info('Signalling that we want to cancel anything that\'s running.')
      self.signalling_future.cancel()

    # Wait until the asynchronous task is done
    if not self.async_task.done():
      self.log.info('Blocking until async task is done.')

      loop = asyncio.get_event_loop()

      try:
        loop.run_until_complete(self.async_task)
      except asyncio.CancelledError:
        self.log.info('Asynchronous task was cancelled')
        return

    try:
      self.async_task.result() # This re-raises any exception of the task
    except asyncio.CancelledError:
      self.log.info('Asynchronous task was cancelled')
    except Exception:
      self.log.exception('Exception from asynchronous task')

def register():
  bpy.utils.register_class(AsyncLoopModalOperator)

def unregister():
  bpy.utils.unregister_class(AsyncLoopModalOperator)
