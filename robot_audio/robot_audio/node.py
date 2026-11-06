"""Service-only control surface. Playback never blocks a ROS callback."""
import rclpy
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from std_srvs.srv import Trigger
from audio_common_msgs.srv import PlayAudio, SayAudio, AudioStatus, ListAudio
from .controller import AudioController


class SpeakerNode(Node):
    def __init__(self, **kwargs):
        super().__init__('audio', **kwargs)
        defaults = dict(clips_dir='', voices_dir='', device='plughw:CARD=UACDemoV10,DEV=0',
                        piper_binary='/opt/piper/piper', default_voice='en_US-lessac-medium',
                        gain=0.7, processing_timeout_sec=60.0)
        for key, value in defaults.items(): self.declare_parameter(key, value)
        value = lambda name: self.get_parameter(name).value
        self.controller = AudioController(value('clips_dir'), value('voices_dir'), device=value('device'),
            piper=value('piper_binary'), default_voice=value('default_voice'), gain=value('gain'),
            timeout=value('processing_timeout_sec'))
        self.create_service(PlayAudio, '~/play', self.play)
        self.create_service(SayAudio, '~/say', self.say)
        self.create_service(Trigger, '~/stop', self.stop)
        self.create_service(AudioStatus, '~/status', self.status)
        self.create_service(ListAudio, '~/list_clips', self.list_clips)
        self._last_state = None
        self.create_timer(.2, self.log_status)
        self.get_logger().info('USB audio ready: Piper required; playback awaits a service request')

    @staticmethod
    def _accept(response, submit):
        try:
            response.job_id = submit()
            response.success, response.message = True, 'Accepted; query status for completion'
        except Exception as error:
            response.success, response.message = False, str(error)
        return response

    def play(self, request, response):
        return self._accept(response, lambda: self.controller.play(request.clip, request.loop, request.interrupt))

    def say(self, request, response):
        return self._accept(response, lambda: self.controller.say(request.text, request.voice,
                                                                 request.loop, request.interrupt))

    def stop(self, request, response):
        self.controller.stop()
        response.success, response.message = True, 'Stopped active work and cleared pending requests'
        return response

    def status(self, request, response):
        response.success = True
        for key, value in self.controller.status().items(): setattr(response, key, value)
        return response

    def list_clips(self, request, response):
        try:
            response.clips, response.voices = self.controller.list_assets()
            response.default_voice = self.controller.default_voice
            response.success = True
        except Exception as error:
            response.success, response.message = False, str(error)
        return response

    def log_status(self):
        status = self.controller.status()
        key = (status['job_id'], status['state'])
        if key != self._last_state:
            self._last_state = key
            log = self.get_logger().error if status['last_error'] else self.get_logger().info
            log(f"Audio job={status['job_id']} state={status['state']} {status['last_error']}")

    def destroy_node(self):
        self.controller.close()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = SpeakerNode()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None: node.destroy_node()
        if rclpy.ok(): rclpy.shutdown()
