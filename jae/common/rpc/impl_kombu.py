#!/usr/bin/env python
# This file is used for rpc

import kombu
import kombu.entity
import kombu.messaging
from eventlet import greenpool

import sys
import time
import eventlet
import greenlet
import itertools

from jae.common.rpc import base


class Pool(pools.Pool):
    """Class that implements a Pool of Connections."""

    def __init__(self, conf, connection_cls, *args, **kwargs):
        self.connection_cls = connection_cls
        self.conf = conf
        self.rpc_conn_pool_size = 100
        kwargs.setdefault("max_size", self.rpc_conn_pool_size)
        kwargs.setdefault("order_as_stack", True)
        super(Pool, self).__init__(*args, **kwargs)

    def create(self):
        return self.connection_cls(self.conf)


class ConsumerBase(object):
    """Consumer base class"""

    def __init__(self, channel, callback, tag, **kwargs):
        self.callback = callback
        self.tag = str(tag)
        self.channel = channel
        self.kwargs['channel'] = channel
        self.queue = kombu.entity.Queue(**self.kwargs)
        self.queue.declare()

    def consume(self, *args, **kwargs):
        """"""
        options = {'consumer_tag': self.tag}
        options['nowait'] = kwargs.get('nowait', False)
        callback = kwargs.get('callback', self.callback)
        if not callback:
            raise ValueError("No callback defined")

        def _callback(raw_message):
            message = self.channel.message_to_python(raw_message)
            try:
                callback(message.payload)
                message.ack()
            except Exception:
                LOG.exception("Failed to process message... skipping it.")

        self.queue.consume(*args, callback=_callback, **options)


class FanoutConsumer(ConsumerBase):
    """Consumer class for 'fanout'"""

    def __init__(self, conf, channel, topic, callback, tag, **kwargs):
        """Init a 'fanout' consumer"""
        unique = uuid.uuid4().hex
        exchange_name = '%s_fanout' % topic
        queue_name = '%s_fanout_%s' % (topic, unique)

        # Default options
        options = {'durable': False,
                   'auto_delete': True,
                   'exclusive': True}
        options.update(kwargs)
        exchange = kombu.entity.Exchange(name=exchange_name, type='fanout',
                                         durable=options['durable'],
                                         auto_delete=options['auto_delete'])

        super(FanoutConsumer, self).__init__(channel, callback, tag,
                                             name=queue_name,
                                             exchange=exchange,
                                             routing_key=topic,
                                             **options)

class TopicConsumer(ConsumerBase):
    """Consumer class for 'topic'"""
   
    def __init__(self, conf, channel, topic, callback, tag, name=None,**kwargs):
        """Init a topic consumer"""
        options = {'durable': conf.rabbit_durable_queues,
                   'auto_delete': False,
                   'exclusive': False}
        options.update(kwargs)
        exchange = kombu.entity.Exchange(name=conf.control_exchange,
                                         type='topic',
                                         durable=options['durable'],
                                         auto_delete=options['auto_delete'])
        super(TopicConsumer, self).__init__(channel,
                                            callback,
                                            tag,
                                            name=name or topic,
                                            exchange=exchange,
                                            routing_key=topic,
                                            **options)


class Publisher(object):
    """Base publisher class"""

    def __init__(self, channel, exchange_name, routing_key, **kwargs)
        """Init the publisher class with exchange_name and routing_key"""
        self.channel = channel
        self.exchange_name = exchange_name
        self.routing_key = routing_key
        self.kwargs = kwargs

    def connect(self):
        self.exchange = kombu.entity.Exchange(name=self.exchange_name,
                                              **self.kwargs)
        self.producer = kombu.messaging.Producer(exchange=self.exchange,
                                                 channel=channel,
                                                 routing_key=self.routing_key)
    def send(self, msg):
        """Send a message"""
        self.producer.publish(msg)

class TopicPublisher(Publisher):
    """Publisher class for topic"""
    def __init__(self,conf, channel, topic):
        """init a 'topic' publisher.

        Kombu options may be passed as keyword args to override defaults
        """
        options = {'durable': conf.rabbit_durable_queues,
                   'auto_delete': False,
                   'exclusive': False} 
        options.update(kwargs)
        super(TopicPublisher, self).__init__(channel, conf.control_exchange,
                                             topic, type='topic', **options)


class Connection(object):
    def __init__(self, conf):
        self.conf = conf
        self.max_retries = 5
        self.retry_interval = 5.0
        self.consumers = []
        self.consumer_thread = None
        self.consumer_num = itertools.count(1)

        params = {}
        params.setdefault('hostname', self.conf.rabbit_host)
        params.setdefault('port', self.conf.rabbit_port)
        params.setdefault('userid', self.conf.rabbit_userid)
        params.setdefault('password', self.conf.rabbit_password)
        params.setdefault('virtual_host', self.conf.rabbit_virtual_host)

        self.params = params
        self.connection = None
        self.channel = None

        self.reconnect()

    def _connect(self):
        """Connect to rabbit"""
        if self.connection is not None:
            LOG.info("Connecting to AMQP server on %(hostname)s:%(port)d" % self.params)
            try:
                self.connection.close()
            except:
                pass
            self.connection = None
        self.connection = kombu.connection.BrokerConnection(**self.params)
        self.connection.connect()
        self.channel = self.connection.channel()


    def reconnect(self):
        """"""
        attempt = 0
        while True:
            attempt += 1
            try:
                self._connect()
                return
            except:
                LOG.info("Connecting to AMQP failed...retry") 
    
            if attempt >= self.max_retries:
                LOG.info("Connecting to AMQP server on %(hostname)s:%(port)d falied" % self.params)
                sys.exit(1)
            time.sleep(self.retry_interval)
    
    
    def ensure(self, error_callback, method, *args, **kwargs):
        """Make sure the method was invoked succeed"""
        while True:
            try:
                return method(*args, **kwargs)
            except Exception as ex:
                error_callback(ex)
    
            self.reconnect()
    
    
    def consume_in_thread(self):
        """Start consume in a greenthread"""
    
        def _consumer_thread():
            try:
                self.consume()
            except greenlet.GreenletExit:
                return
    
        if self.consumer_thread is None:
            self.consumer_thread = eventlet.spawn(_consumer_thread)
        return self.consumer_thread
    
    
    def publisher_send(self,publisher_cls,topic,msg):
        """Send message from publisher"""
        def _error_callback(exc):
            LOG.exception("Send topic messsage failed")
        def _publish():
            publisher = publisher_cls(self.conf, self.channel, topic)
            publisher.send(msg)

        self.ensure(_error_callback,_publish)

    def topic_send(self, topic, msg):
        """Send a 'topic' message"""
        self.publisher_send(TopicPublisher, topic, msg)

    def declare_consumer(self, consumer_cls, topic, callback):
        """Create a Consumer using the class that was passed in and
           add it to our list of consumers"""
    
        def _connect_error(exc):
            log_info = {'topic': topic, 'err_str': str(exc)}
            LOG.error("Failed to declare consumer for topic '%(topic)s': %(err_str)s" % log_info)
    
        def _declare_consumer():
            consumer = consumer_cls(self.conf, self.channel, topic, callback, self.consumer_num.next())
            self.consumers.append(consumer)
            return consumer
    
        return self.ensure(_connect_error, _declare_consumer)
    
    
    def declare_fanout_consumer(self, topic, callback):
        """Create a 'fanout' consumer"""
        self.declare_consumer(FanoutConsumer, topic, callback)
    
    
    def declare_topic_consumer(self, topic, callback):
        """Create a 'topic' consumer"""
        self.declare_consumer(TopicConsumer, topic, callback)
    
    
    def create_consumer(self, topic, proxy, fanout=False):
        """Create a consumer that calls a method in a proxy object"""
        callback = ProxyCallback(proxy)
        if fanout:
            self.declare_fanout_consumer(topic, callback)
        else:
            self.declare_topic_consumer(topic, callback)


class ConnectionContext(base.Connection):
    def __init__(self, conf, connection_pool, pooled=True):
        self.conf = conf
        self.connection_pool = connection_pool
        self.pooled = pooled

        if self.pooled:
            self.connection = self.connection_pool.get()
        else:
            self.connection = self.connection_pool.connection_cls(self.conf)

    def __enter__(self):
        """When with ConnectionContext() is used, return self"""
        return self

    def __exit__(self, exc_type, exc_value, tb):
        """End of 'with' statement."""
        self.connection.close()

    def create_consumer(self, topic, proxy, fanout=False):
        self.connection.create_consumer(topic, proxy, fanout)

    def consume_in_thread(self):
        self.connection.consume_in_thread()

    def __getattr__(self, key):
        """Get method from self.connection"""
        if self.connection:
            return getattr(self.connection, key)
        else:
            LOG.exception("Connection failed")

class ProxyCallback(object):
    def __init__(self, proxy):
        self.proxy = proxy
        self.pool = greenpool.GreenPool()

    def __call__(self, message):
        method = message.pop('method')
        if not message:
            LOG.warn("no method for message %s process" % message)
            return
        args = message.pop("args", {})
        self.pool.spawn_n(self._process_data, method, args)

    def _process_data(self, method, args):
        try:
            self.proxy.dispatch(method, args)
        except Exception:
            LOG.exception("Something goes wrong during message handling")


def create_connection(conf, new=True, connection_pool):
    """Create a connection context manager"""
    return ConnectionContext(conf, connection_pool, pooled=not new)


def cast(conf, topic, msg, connection_pool):
    """Send a message on a topic without waiting for a response"""
    with ConnectionContext(conf, connection_pool) as conn:
        conn.topic_send(topic, msg)

# class Connection(object):
#    def __init__(self):
#        self.connection = kombu.connection.BrokerConnection(hostname='localhost',
#                                                            port=5672,
#                                                            userid='guest',
#                                                            password='guest',
#                                                            virtual_host='/')
#        self.channel = self.connection.channel()
#
#def Publisher(object):
#    """Base Publisher Class"""
#    def __init__(self,channel,exchange_name,routing_key,**kwargs):
#        """Init the Publisher class with the exchange_name,routing_key,
#           and kwargs options
#        """
#        self.channel = channel
#        self.exchange_name = exchange_name
#        self.routing_key = routing_key
#        self.kwargs = kwargs
#        self.exchange = kombu.entity.Exchange(name=self.exchange_name,
#                                              **self.kwargs)
#        self.producer = kombu.messaging.Producer(exchange=self.exchange,
#                                                 channel=self.channel,
#                                                 routing_key=self.routing_key)
#
#    def send(self,msg):
#        self.producer.publish(msg)
#
#def TopicPublisher(Publisher):
#    """Topic Publisher Class"""
#    def __init__(self,channel,topic,**kwargs):
#        """Init a 'topic' publisher"""
#        options = {'durable': True,
#                   'auto_delete': False,
#                   'exclusive': False}
#        super(TopicPublisher,self).__init__(channel)
#
#class TopicConsumer(object):
#    def __init__(self,channel,topic,callback):
#        self.channel = channel
#        self.callback = callback
#        self.exchange = kombu.entity.Exchange(name='container',
#                                              type='topic',
#                                              durable=True,
#                                              auto_delete=False)
#        self.queue = kombu.entity.Queue(name=topic,
#                                        exchange=self.exchange,
#                                        routing_key=topic,
#                                        channel=self.channel,
#                                        durable=False,
#                                        auto_delete=False,
#                                        exclusive=False)
#        self.queue.declare()
#
#    def consume(self):
#        self.queue.consume(callback=None)
#
#
#
#def call(msg):
#    return NotImplementedError()
#
#
#def cast(msg):
#    pass
