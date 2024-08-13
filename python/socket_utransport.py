"""
SPDX-FileCopyrightText: 2024 Contributors to the Eclipse Foundation

See the NOTICE file(s) distributed with this work for additional
information regarding copyright ownership.

This program and the accompanying materials are made available under the
terms of the Apache License Version 2.0 which is available at

    http://www.apache.org/licenses/LICENSE-2.0

SPDX-License-Identifier: Apache-2.0
"""

import asyncio
import logging
import socket
import threading
from threading import Lock
from typing import Dict, Tuple

from uprotocol.transport.ulistener import UListener
from uprotocol.transport.utransport import UTransport
from uprotocol.uri.factory.uri_factory import UriFactory
from uprotocol.uri.serializer.uriserializer import UriSerializer
from uprotocol.uri.validator.urivalidator import UriValidator
from uprotocol.v1.ucode_pb2 import UCode
from uprotocol.v1.umessage_pb2 import UMessage
from uprotocol.v1.uri_pb2 import UUri
from uprotocol.v1.ustatus_pb2 import UStatus

logger = logging.getLogger(__name__)
DISPATCHER_ADDR: tuple = ("127.0.0.1", 44444)
BYTES_MSG_LENGTH: int = 32767


def get_uuri_string(uri: UUri) -> str:
    """
     Serializes the UUri
    :param uri:  to serialize
    :return: Serialized uri string
    """
    if uri is None:
        return ''
    return UriSerializer.serialize(uri)


def matches(source: str, sink: str, attributes):
    """
    Matches the given attributes with the source and sink URIs.

    :param source: The source Uri to match.
    :param sink: The sink Uri to match.
    :param attributes: The attributes to match.
    :return: Returns True if the attributes match the source and sink URIs.
    """
    if attributes is None:
        return False
    source = UriSerializer.deserialize(source)
    sink = UriSerializer.deserialize(sink)
    if source == UriFactory.ANY:
        return UriValidator.matches(sink, attributes.sink)
    elif sink == UriFactory.ANY:
        return UriValidator.matches(source, attributes.source)
    else:
        return UriValidator.matches(source, attributes.source) and UriValidator.matches(sink, attributes.sink)


class SocketUTransport(UTransport):
    def __init__(self, source: UUri):
        """
        Initializes a SocketUTransport instance and establishes a connection to the dispatcher.
        Sets up the socket and begins listening for incoming messages asynchronously.

        param source: The URI associated with the UTransport.
        """

        self.source = source
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.connect(DISPATCHER_ADDR)
        self.uri_to_listener: Dict[Tuple[str, str], UListener] = {}
        self.lock = Lock()
        thread = threading.Thread(target=self.__listen)
        thread.start()

    def __listen(self):
        """
        Listens to incoming UMessages from the Dispatcher.
        Processes incoming data if the listener is registered to a UMessage source and sink UURI filter.
        """
        while True:
            try:
                recv_data = self.socket.recv(BYTES_MSG_LENGTH)

                if not recv_data or recv_data == b"":
                    self.socket.close()
                    return
                umsg = UMessage()
                umsg.ParseFromString(recv_data)
                print('received message', umsg)
                logger.info(f"{self.__class__.__name__} Received uMessage")
                self._notify_listeners(umsg)

            except socket.error as e:
                logger.error(f"Socket error: {e}")
                self.socket.close()
                break
            except Exception as e:
                logger.error(f"Unexpected error: {e}")

    def _notify_listeners(self, umsg):
        """
        Notifies listeners registered for the source and sink URI filters about the incoming message.
        The message is matched against the registered URI filters, and the appropriate listeners are
        invoked asynchronously.

        :param umsg: The message to be processed and dispatched to listeners.
        """

        with self.lock:
            is_match = False
            for (source_uri, sink_uri), listener in self.uri_to_listener.items():
                is_match = matches(source_uri, sink_uri, umsg.attributes)
                if is_match and listener is not None:
                    print('uri notify')
                    logger.info(f"{self.__class__.__name__} Handle Uri")
                    asyncio.run(listener.on_receive(umsg))
            if not is_match:
                print('uri not match')

                logger.info(f"{self.__class__.__name__} Uri not found in Listener Map, discarding...")

    async def send(self, message: UMessage) -> UStatus:
        """
        Sends the provided UMessage over the socket connection.

        :param message: The UMessage to be sent.
        :return: A status indicating the outcome of the send operation.
        """
        umsg_serialized: bytes = message.SerializeToString()
        try:
            print('send', message)
            self.socket.sendall(umsg_serialized)
            logger.info("uMessage Sent to dispatcher from python socket transport")
        except OSError as e:
            logger.exception(f"INTERNAL ERROR: {e}")
            return UStatus(code=UCode.INTERNAL, message=f"INTERNAL ERROR: {e}")
        return UStatus(code=UCode.OK, message="OK")

    async def register_listener(self, source_filter: UUri, listener: UListener, sink_filer: UUri = None) -> UStatus:
        """
        Registers the specified listener for the given source and sink URI filters.

        :param source_filter: The URI filter for the source.
        :param listener: The listener to be registered.
        :param sink_filer: The URI filter for the sink.
        :return: A status indicating the outcome of the register listener operation.
        """
        source_uri = get_uuri_string(source_filter)
        sink_uri = get_uuri_string(sink_filer)
        print('listeners', source_uri, sink_uri, listener)
        self.uri_to_listener[source_uri, sink_uri] = listener
        return UStatus(code=UCode.OK, message="OK")

    async def unregister_listener(self, source_filter: UUri, listener: UListener, sink_filer: UUri = None) -> UStatus:
        """
        Unregisters the specified listener from the given source and sink URI filters.

        :param source_filter: The URI filter for the source.
        :param listener: The listener to be removed.
        :param sink_filer: The URI filter for the sink.
        :return: A status indicating the outcome of the unregister listener operation.
        """

        source_uri = get_uuri_string(source_filter)
        sink_uri = get_uuri_string(sink_filer)

        listener = self.uri_to_listener.pop((source_uri, sink_uri), None)
        if listener:
            return UStatus(code=UCode.OK, message="Listener removed successfully")
        else:
            return UStatus(code=UCode.NOT_FOUND, message="Listener not found for the given UUri")

    def get_source(self) -> UUri:
        """
        Returns the source URI of the UTransport.
        """
        return self.source

    def close(self):
        """
        Closes the socket connection and releases any resources associated with it..
        """
        self.socket.close()
        logger.info(f"{self.__class__.__name__} Socket Connection Closed")
