import asyncio
import logging
import bpy
from bpy import context

from . import async_loop

log = logging.getLogger(__name__)

def sample_animation(object):
  """
  Returns a list of the position and orientation for the given object for every
  frame of the animation.
  """
  start_frame_index = bpy.context.scene.frame_current

  poses = []
  for frame_index in range(bpy.context.scene.frame_start, bpy.context.scene.frame_end):
    bpy.context.scene.frame_set(frame_index)
    pos, rotQ, scale = object.matrix_world.decompose()
    rot = rotQ.to_euler('ZYX')
    poses.append([pos.x, pos.y, pos.z, rot.x, rot.y, rot.z])

  bpy.context.scene.frame_current = start_frame_index

  return poses

class AnimationServerOperator(async_loop.AsyncModalOperatorMixin, bpy.types.Operator):
  """Runs a server to serve requests for information about the active animation."""

  bl_idname = 'animation_server.operator'
  bl_label = 'Start Animation Server'

  log = logging.getLogger(f'{__name__}.AnimationServerOperator')

  def invoke(self, context, event):
    return async_loop.AsyncModalOperatorMixin.invoke(self, context, event)

  def modal(self, context, event):
    if not event.type == 'TIMER':
      return {'PASS_THROUGH'}

    # Let async stuff happen
    result = async_loop.AsyncModalOperatorMixin.modal(self, context, event)

    if not {'PASS_THROUGH', 'RUNNING_MODAL'}.intersection(result):
      return result

    return {'RUNNING_MODAL'}

  async def async_execute(self, context):
    """Main entrypoint for async server."""
    self.log.info('Running server')

    await asyncio.start_server(self._handle_request, '127.0.0.1', 8889)

    # Stay alive indefinitely
    # TODO: provide a means to shut down the server
    while True:
      await asyncio.sleep(1)

  async def _handle_request(self, reader, writer):
    """
    Callback that handles an individual client of the animation server.

    Requests are HTTP-like, such as "GET /animation 1".
    """

    # Read the request
    dataReceived = await reader.readline()
    message = dataReceived.decode()

    self.log.debug(f'Request:\n{message}')

    # Handle the request

    message_parts = message.split()

    if not len(message_parts) == 2:
      writer.write('Error: Bad request'.encode())
      writer.close()
      return

    method, path = message_parts

    if method == None or path == None:
      log.warn(f'Received bad request: {message}')

    reply = 'Error: Unknown request'

    if method == 'GET':
      if path == '/animation':
        fps = bpy.context.scene.render.fps
        poses = sample_animation(bpy.context.object)
        pose_lines = [','.join(['{0:0.2f}'.format(v) for v in p]) for p in poses]
        pose_listing = '\n'.join(pose_lines)
        reply = f'frameCount={len(pose_lines)},fps={fps}\n{pose_listing}\n'

    # Reply to the request
    dataToSend = reply.encode()

    self.log.debug(f'Writing {len(dataToSend)} byte reply')

    writer.write(dataToSend)
    await writer.drain()

    writer.close()

  def _finish(self, context):
    self.log.debug('Finishing the modal operator')
    async_loop.AsyncModalOperatorMixin._finish(self, context)
    self.log.debug('Modal operator finished')

def register():
  bpy.utils.register_class(AnimationServerOperator)

def unregister():
  bpy.utils.unregister_class(AnimationServerOperator)
