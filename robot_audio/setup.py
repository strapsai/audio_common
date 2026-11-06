from glob import glob
from setuptools import setup

setup(name='robot_audio', version='0.1.0', packages=['robot_audio'],
      package_data={'robot_audio': ['voices.json', 'clips/*.wav', 'clips/*.json']},
      data_files=[('share/ament_index/resource_index/packages', ['resource/robot_audio']),
                  ('share/robot_audio', ['package.xml', 'README.md']),
                  ('share/robot_audio/launch', glob('launch/*.py'))],
      install_requires=['setuptools'], tests_require=['pytest'], python_requires='>=3.10', zip_safe=False,
      maintainer='AirLab', maintainer_email='airlab@andrew.cmu.edu', license='MIT',
      description='Interruptible USB speaker playback and offline Piper speech services',
      entry_points={'console_scripts': [
          'speaker_node = robot_audio.node:main',
          'speaker_service_bridge = robot_audio.service_bridge:main',
          'download_voices = robot_audio.download_voices:main']})
