#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

import sys

import eventlet

eventlet.monkey_patch(
    os=True,
    select=True,
    socket=True,
    thread=False if '--use-debugger' in sys.argv else True,
    time=True)

import os

# If ../mistral/__init__.py exists, add ../ to Python search path, so that
# it will override what happens to be installed in /usr/(local/)lib/python...
POSSIBLE_TOPDIR = os.path.normpath(os.path.join(os.path.abspath(sys.argv[0]),
                                   os.pardir,
                                   os.pardir))
if os.path.exists(os.path.join(POSSIBLE_TOPDIR, 'mistral', '__init__.py')):
    sys.path.insert(0, POSSIBLE_TOPDIR)

from oslo.config import cfg
from oslo import messaging
from wsgiref import simple_server

from mistral.api import app
from mistral import config
from mistral import context
from mistral import engine
from mistral.engine import executor
from mistral.openstack.common import log as logging


LOG = logging.getLogger(__name__)


def launch_executor(transport):
    serializer = context.RpcContextSerializer(context.JsonPayloadSerializer())
    target = messaging.Target(topic=cfg.CONF.executor.topic,
                              server=cfg.CONF.executor.host)
    # Since engine and executor are tightly coupled, use the engine
    # configuration to decide which executor to get.
    endpoints = [executor.get_executor(cfg.CONF.engine.engine, transport)]
    server = messaging.get_rpc_server(
        transport, target, endpoints, executor='eventlet',
        serializer=serializer)
    server.start()
    server.wait()


def launch_engine(transport):
    serializer = context.RpcContextSerializer(context.JsonPayloadSerializer())
    target = messaging.Target(topic=cfg.CONF.engine.topic,
                              server=cfg.CONF.engine.host)
    endpoints = [engine.get_engine(cfg.CONF.engine.engine, transport)]
    server = messaging.get_rpc_server(
        transport, target, endpoints, executor='eventlet',
        serializer=serializer)
    server.start()
    server.wait()


def launch_api(transport):
    host = cfg.CONF.api.host
    port = cfg.CONF.api.port
    server = simple_server.make_server(host, port,
                                       app.setup_app(transport=transport))
    LOG.info("Mistral API is serving on http://%s:%s (PID=%s)" %
             (host, port, os.getpid()))
    server.serve_forever()


def launch_any(transport, options):
    # Launch the servers on different threads.
    threads = [eventlet.spawn(LAUNCH_OPTIONS[option], transport)
               for option in options]
    [thread.wait() for thread in threads]


# Map cli options to appropriate functions. The cli options are
# registered in mistral's config.py.
LAUNCH_OPTIONS = {
    'api': launch_api,
    'engine': launch_engine,
    'executor': launch_executor
}


def main():
    try:
        config.parse_args()
        logging.setup('Mistral')

        # Please refer to the oslo.messaging documentation for transport
        # configuration. The default transport for oslo.messaging is
        # rabbitMQ. The available transport drivers are listed in the
        # setup.cfg file in oslo.messaging under the entry_points section for
        # oslo.messaging.drivers. The transport driver is specified using the
        # rpc_backend option in the default section of the oslo configuration
        # file. The expected value for the rpc_backend is one of the key
        # values available for the oslo.messaging.drivers (i.e. rabbit, fake).
        # There are additional options such as ssl and credential that can be
        # specified depending on the driver.  Please refer to the driver
        # implementation for those additional options. It's important to note
        # that the "fake" transport should only be used if "all" the Mistral
        # servers are launched on the same process. Otherwise, messages do not
        # get delivered if the Mistral servers are launched on different
        # processes because the "fake" transport is using an in process queue.
        transport = messaging.get_transport(cfg.CONF)

        if (cfg.CONF.server == ['all']):
            # Launch all servers.
            launch_any(transport, LAUNCH_OPTIONS.keys())
        else:
            # Validate launch option.
            if set(cfg.CONF.server) - set(LAUNCH_OPTIONS.keys()):
                raise Exception('Valid options are all or any combination of '
                                'api, engine, and executor.')

            # Launch distinct set of server(s).
            launch_any(transport, set(cfg.CONF.server))

    except RuntimeError as excp:
        sys.stderr.write("ERROR: %s\n" % excp)
        sys.exit(1)


if __name__ == '__main__':
    main()
