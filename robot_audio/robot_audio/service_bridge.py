"""Explicit service proxies from the base domain to two robot audio servers.

The stock domain_bridge executable's YAML path bridges topics, not arbitrary
service types. These fixed-type proxies use independent ROS contexts/executors.
"""
import threading

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.context import Context
from rclpy.executors import MultiThreadedExecutor, ExternalShutdownException
from rclpy.node import Node
from std_srvs.srv import Trigger
from audio_common_msgs.srv import PlayAudio, SayAudio, AudioStatus, ListAudio

SERVICES = {'play': PlayAudio, 'say': SayAudio, 'stop': Trigger,
            'status': AudioStatus, 'list_clips': ListAudio}


class AudioServiceBridge(Node):
    def __init__(self, *, context=None, **kwargs):
        super().__init__('spot_audio_service_bridge', context=context, **kwargs)
        self.declare_parameter('spot1_domain', 10)
        self.declare_parameter('spot2_domain', 20)
        self.declare_parameter('request_timeout_sec', 3.0)
        self.timeout = float(self.get_parameter('request_timeout_sec').value)
        if not 0 < self.timeout <= 30:
            raise ValueError('request_timeout_sec must be in (0, 30]')
        self._group = ReentrantCallbackGroup()
        self._remotes = []
        self._proxy_services = []
        base_domain = self.context.get_domain_id()
        domains = [int(self.get_parameter(f'{robot}_domain').value) for robot in ('spot1', 'spot2')]
        if any(not 0 <= d <= 232 or d == base_domain for d in domains) or len(set(domains)) != 2:
            raise ValueError('Use distinct base/Spot ROS domain IDs in [0, 232]')
        try:
            for robot, domain in zip(('spot1', 'spot2'), domains):
                remote_context = Context()
                rclpy.init(args=[], context=remote_context, domain_id=domain)
                remote = Node(f'{robot}_audio_proxy_client', context=remote_context, use_global_arguments=False)
                executor = MultiThreadedExecutor(num_threads=2, context=remote_context)
                executor.add_node(remote)
                worker = threading.Thread(target=executor.spin, name=f'{robot}-audio-domain', daemon=True)
                self._remotes.append((remote_context, remote, executor, worker))
                worker.start()
                for name, service_type in SERVICES.items():
                    path = f'/{robot}/audio/{name}'
                    client = remote.create_client(service_type, path)
                    self._proxy_services.append(self.create_service(service_type, path,
                        self._forward(client), callback_group=self._group))
        except Exception:
            self._close_remotes()
            raise

    def _forward(self, client):
        def callback(request, response):
            if not client.service_is_ready():
                response.success, response.message = False, 'Robot audio service is unavailable'
                return response
            try:
                future = client.call_async(request)
                done = threading.Event()
                future.add_done_callback(lambda _: done.set())
                if not done.wait(self.timeout):
                    client.remove_pending_request(future)
                    response.success = False
                    response.message = ('Robot response timed out; command outcome unknown. '
                                        'Query status; do not blindly resend play/say.')
                    return response
                result = future.result()
                if result is None:
                    raise RuntimeError('Robot returned no response')
                return result
            except Exception as error:
                response.success, response.message = False, str(error)
                return response
        return callback

    def _close_remotes(self):
        for context, node, executor, worker in self._remotes:
            executor.shutdown(timeout_sec=2.)
            node.destroy_node()
            if context.ok(): context.shutdown()
            worker.join(timeout=2.)
        self._remotes.clear()

    def destroy_node(self):
        self._close_remotes()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    bridge = None
    executor = None
    try:
        bridge = AudioServiceBridge()
        # Separate callbacks let stop/status proceed while another proxy times out.
        executor = MultiThreadedExecutor(num_threads=8)
        executor.add_node(bridge)
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if executor is not None: executor.shutdown(timeout_sec=2.)
        if bridge is not None: bridge.destroy_node()
        if rclpy.ok(): rclpy.shutdown()
