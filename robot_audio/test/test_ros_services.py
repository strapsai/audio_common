"""Real ROS services, two robot domains and a base domain; fake audio processes."""
import os
import threading
import time

import pytest
rclpy=pytest.importorskip('rclpy')
from rclpy.context import Context
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from audio_common_msgs.srv import PlayAudio,SayAudio,AudioStatus,ListAudio
from std_srvs.srv import Trigger
from robot_audio.node import SpeakerNode
from robot_audio.service_bridge import AudioServiceBridge
from test_controller import rig,wait


def test_two_robots_through_base_service_proxy(rig,monkeypatch):
    assert set(os.listdir('/sys/class/net')) == {'lo'}, 'ROS test requires a network-none container'
    controller,events,clips,voices,binpath=rig
    monkeypatch.setenv('PATH',str(binpath)+os.pathsep+os.environ['PATH'])
    contexts=[];nodes=[];executors=[];workers=[]
    try:
        for domain in (201,202,203):
            context=Context();rclpy.init(args=[],context=context,domain_id=domain);contexts.append(context)
        params=[Parameter('clips_dir',value=str(clips)),Parameter('voices_dir',value=str(voices)),
                Parameter('piper_binary',value=str(binpath/'piper'))]
        for robot,context in zip(('spot1','spot2'),contexts[1:]):
            node=SpeakerNode(context=context,namespace='/'+robot,parameter_overrides=params)
            nodes.append(node)
            executor=MultiThreadedExecutor(num_threads=2,context=context);executor.add_node(node)
            executors.append(executor)
        bridge=AudioServiceBridge(context=contexts[0],parameter_overrides=[
            Parameter('spot1_domain',value=202),Parameter('spot2_domain',value=203),
            Parameter('request_timeout_sec',value=2.)])
        client_node=Node('audio_test_client',context=contexts[0]);nodes.extend([bridge,client_node])
        executor=MultiThreadedExecutor(num_threads=8,context=contexts[0])
        executor.add_node(bridge);executor.add_node(client_node);executors.append(executor)
        for executor in executors:
            worker=threading.Thread(target=executor.spin,daemon=True);worker.start();workers.append(worker)
        clients={}
        for robot in ('spot1','spot2'):
            for name,kind in [('play',PlayAudio),('say',SayAudio),('stop',Trigger),('status',AudioStatus),('list_clips',ListAudio)]:
                client=client_node.create_client(kind,f'/{robot}/audio/{name}')
                assert client.wait_for_service(timeout_sec=5)
                clients[robot,name]=client
        # The remote service discovery proceeds independently of proxy discovery.
        time.sleep(.5)
        def call(robot,name,request):
            future=clients[robot,name].call_async(request)
            wait(future.done,timeout=5)
            result=future.result();assert result is not None
            return result
        # Retry only read-only discovery/status, never replay side-effectful calls.
        end=time.monotonic()+5
        while not call('spot1','status',AudioStatus.Request()).success:
            assert time.monotonic()<end;time.sleep(.1)
        assets=call('spot1','list_clips',ListAudio.Request())
        assert assets.success and assets.voices==['en_US-lessac-medium','en_US-ryan-medium']
        result=call('spot1','play',PlayAudio.Request(clip='hello.wav',loop=True,interrupt=True))
        assert result.success and result.job_id
        wait(lambda:any(e['tool']=='aplay' for e in events()))
        assert call('spot1','status',AudioStatus.Request()).busy
        assert not call('spot2','status',AudioStatus.Request()).busy
        rejected=call('spot1','play',PlayAudio.Request(clip='../escape.wav'))
        assert not rejected.success
        speech=call('spot2','say',SayAudio.Request(text="It's ROS speech",voice='en_US-ryan-medium'))
        assert speech.success
        end=time.monotonic()+5
        while True:
            state=call('spot2','status',AudioStatus.Request())
            if state.state=='done':break
            assert time.monotonic()<end
            time.sleep(.05)
        assert call('spot1','stop',Trigger.Request()).success
        blocked=call('spot2','say',SayAudio.Request(text='BLOCK synthesis'))
        assert blocked.success
        wait(lambda:any(e.get('text')=='BLOCK synthesis\n' for e in events()))
        start=time.monotonic()
        assert call('spot2','stop',Trigger.Request()).success
        assert time.monotonic()-start<1.
        end=time.monotonic()+5
        while call('spot2','status',AudioStatus.Request()).busy:
            assert time.monotonic()<end;time.sleep(.02)
        assert call('spot2','stop',Trigger.Request()).success  # idempotent
        # A missing remote server must not look like a successful/idle endpoint.
        nodes[1].destroy_node()
        nodes.pop(1)
        result=call('spot2','status',AudioStatus.Request())
        assert not result.success and result.message
    finally:
        for executor in reversed(executors):executor.shutdown(timeout_sec=3)
        for node in reversed(nodes):node.destroy_node()
        for context in reversed(contexts):
            if context.ok():context.shutdown()
        for worker in workers:worker.join(timeout=2)


def test_routed_joy_plays_bundled_clip_once_without_synthesis(rig, monkeypatch):
    from sensor_msgs.msg import Joy
    from rclpy.qos import qos_profile_sensor_data
    assert set(os.listdir('/sys/class/net')) == {'lo'}
    _, events, clips, voices, binpath = rig
    monkeypatch.setenv('PATH', str(binpath)+os.pathsep+os.environ['PATH'])
    context = Context()
    rclpy.init(args=[], context=context, domain_id=204)
    speaker = SpeakerNode(context=context, namespace='/spot2', parameter_overrides=[
        Parameter('clips_dir', value=str(clips)), Parameter('voices_dir', value=str(voices)),
        Parameter('piper_binary', value=str(binpath/'piper')),
        Parameter('hotkey_enabled', value=True),
        Parameter('joy_topic', value='/spot2/base/spot/joy')])
    sender = Node('offline_joy_test', context=context)
    publisher = sender.create_publisher(Joy, '/spot2/base/spot/joy', qos_profile_sensor_data)
    executor = MultiThreadedExecutor(num_threads=2, context=context)
    executor.add_node(speaker)
    executor.add_node(sender)
    worker = threading.Thread(target=executor.spin, daemon=True)
    worker.start()
    try:
        wait(lambda: publisher.get_subscription_count() == 1)
        def send(lt=False, x=False):
            message = Joy()
            message.header.stamp = sender.get_clock().now().to_msg()
            message.axes = [0., 0., -1. if lt else 0., 0., 0., 0., 0., 0.]
            message.buttons = [0]*11
            message.buttons[2] = int(x)
            publisher.publish(message)
            time.sleep(.03)
        send()  # Fresh neutral baseline.
        send(lt=True, x=True)
        wait(lambda: len([e for e in events() if e['tool'] == 'aplay']) == 1)
        for _ in range(4):
            send(lt=True, x=True)
        assert len([e for e in events() if e['tool'] == 'aplay']) == 1
        send(lt=True)
        send(lt=True, x=True)
        wait(lambda: len([e for e in events() if e['tool'] == 'aplay']) == 2)
        assert not any(e['tool'] == 'piper' for e in events())
        assert all(e['source'].endswith('vitals_measurement.wav')
                   for e in events() if e['tool'] == 'sox')
    finally:
        executor.shutdown(timeout_sec=3)
        speaker.destroy_node()
        sender.destroy_node()
        context.shutdown()
        worker.join(timeout=2)
