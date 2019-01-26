bl_info = {
    'name': 'Animation Server',
    'author': 'Noah Trueblood and Owen Trueblood',
    'version': (1, 0, 0),
    'blender': (2, 80, 0),
    'location': 'Addon Preferences panel',
    'description': 'Serves animations to external programs.',
    'category': 'Animation',
}

def register():
    """Late-loads and registers the Blender-dependent submodules."""

    import sys

    # Support reloading
    if '%s.blender' % __name__ in sys.modules:
        import importlib

        def reload_mod(name):
            modname = '%s.%s' % (__name__, name)
            try:
                old_module = sys.modules[modname]
            except KeyError:
                # Wasn't loaded before -- can happen after an upgrade.
                new_module = importlib.import_module(modname)
            else:
                new_module = importlib.reload(old_module)

            sys.modules[modname] = new_module
            return new_module

        async_loop = reload_mod('async_loop')
        animation_server = reload_mod('animation_server')
    else:
        from . import (async_loop, animation_server)

    # TODO: necessary? async_loop.setup_asyncio_executor()
    async_loop.register()

    animation_server.register()

def unregister():
    from . import (async_loop, animation_server)

    async_loop.unregister()
    animation_server.unregister()

if __name__ == '__main__':
    register()
    unregister()
