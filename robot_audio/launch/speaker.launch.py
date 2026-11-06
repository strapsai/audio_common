from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    arguments = dict(robot='spot1', clips_dir='', voices_dir='',
                     default_voice='en_US-lessac-medium', device='plughw:CARD=UACDemoV10,DEV=0',
                     piper_binary='/opt/piper/piper', gain='0.7')
    return LaunchDescription([
        *[DeclareLaunchArgument(name, default_value=value) for name, value in arguments.items()],
        Node(package='robot_audio', executable='speaker_node', name='audio',
             namespace=LaunchConfiguration('robot'), output='screen', parameters=[{
                 name: ParameterValue(LaunchConfiguration(name), value_type=float if name == 'gain' else str)
                 for name in arguments if name != 'robot'}])])
