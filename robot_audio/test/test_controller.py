"""Cancellable process tests; no sound card is opened."""
import json
import os
from pathlib import Path
import time
import wave

import pytest
from robot_audio.controller import AudioController


FAKE = r'''#!/usr/bin/env python3
import json,os,sys,time,wave,signal
from pathlib import Path
name=Path(sys.argv[0]).name
args=sys.argv[1:]
def record(**data):
 with open(os.environ['AUDIO_TEST_LOG'],'a') as f:
  f.write(json.dumps(dict(tool=name,pid=os.getpid(),**data))+'\n')
if name=='piper':
 text=sys.stdin.read()
 record(text=text,model=args[args.index('--model')+1])
 if 'BLOCK' in text:
  signal.signal(signal.SIGTERM,signal.SIG_IGN)
  while True: time.sleep(.05)
 output=args[args.index('--output_file')+1]
elif name=='sox':
 record(source=args[0])
 output=args[args.index('vol')-1]
else:
 with wave.open(args[-1]) as f: record(rate=f.getframerate(),channels=f.getnchannels(),width=f.getsampwidth(),device=args[args.index('-D')+1])
 time.sleep(.03)
 sys.exit(0)
with wave.open(output,'wb') as f:
 f.setnchannels(2); f.setsampwidth(2); f.setframerate(48000); f.writeframes(b'\0'*4800)
'''


def wait(predicate, timeout=4.):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate(): return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for audio state')


@pytest.fixture
def rig(tmp_path,monkeypatch):
    clips,voices,binpath=(tmp_path/x for x in ['clips','voices','bin'])
    for path in (clips,voices,binpath): path.mkdir()
    for voice in ['en_US-lessac-medium','en_US-ryan-medium']:
        (voices/(voice+'.onnx')).write_bytes(b'model')
        (voices/(voice+'.onnx.json')).write_text(json.dumps({'audio':{'sample_rate':22050}}))
    for tool in ['piper','sox','aplay']:
        path=binpath/tool; path.write_text(FAKE); path.chmod(0o755)
    for name in ['hello.wav','other.wav']:
        with wave.open(str(clips/name),'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0'*1600)
    logfile=tmp_path/'events.jsonl'
    monkeypatch.setenv('AUDIO_TEST_LOG',str(logfile))
    controller=AudioController(clips,voices,piper=str(binpath/'piper'),player=str(binpath/'aplay'),
                               converter=str(binpath/'sox'),max_queue=2)
    def events():
        return [json.loads(line) for line in logfile.read_text().splitlines()] if logfile.exists() else []
    yield controller,events,clips,voices,binpath
    controller.close()


def test_file_and_voice_jobs(rig):
    c,events,*_=rig
    job=c.play('hello.wav')
    wait(lambda:c.status()['job_id']==job and c.status()['state']=='done')
    output=[e for e in events() if e['tool']=='aplay'][0]
    assert (output['rate'],output['channels'],output['width'])==(48000,2,2)
    assert output['device']=='plughw:CARD=UACDemoV10,DEV=0'
    job=c.say("It's a test; $(not-a-shell)",voice='en_US-ryan-medium')
    wait(lambda:c.status()['job_id']==job and c.status()['state']=='done')
    speech=[e for e in events() if e['tool']=='piper'][-1]
    assert speech['text']=="It's a test; $(not-a-shell)\n"
    assert speech['model'].endswith('en_US-ryan-medium.onnx')


def test_loop_stop_clears_queue(rig):
    c,events,*_=rig
    c.play('hello.wav',loop=True)
    wait(lambda:len([e for e in events() if e['tool']=='aplay'])>=2)
    c.play('other.wav',interrupt=False)
    assert c.status()['queued']==1
    before=time.monotonic();c.stop()
    wait(lambda:not c.status()['busy'])
    assert time.monotonic()-before<.6
    assert c.status()['state']=='stopped' and c.status()['queued']==0
    count=len(events());time.sleep(.15);assert len(events())==count


def test_stop_during_piper_and_next_job_is_not_cancelled(rig):
    c,events,*_=rig
    c.say('BLOCK synthesis')
    wait(lambda:any(e['tool']=='piper' for e in events()))
    pid=[e['pid'] for e in events() if e['tool']=='piper'][-1]
    before=time.monotonic();c.stop()
    wait(lambda:not c.status()['busy'])
    assert time.monotonic()-before<.7
    assert not Path(f'/proc/{pid}').exists()
    job=c.say('Recovery')
    wait(lambda:c.status()['job_id']==job and c.status()['state']=='done')


def test_interrupt_replaces_loop_and_queued_work(rig):
    c,events,*_=rig
    c.play('hello.wav',loop=True)
    wait(lambda:any(e['tool']=='aplay' for e in events()))
    c.say('Discard this',interrupt=False)
    job=c.say('Replacement',interrupt=True)
    wait(lambda:c.status()['job_id']==job and c.status()['state']=='done')
    assert [e['text'] for e in events() if e['tool']=='piper']==['Replacement\n']


def test_queue_bound_and_invalid_requests_preserve_active_job(rig):
    c,events,clips,voices,_=rig
    job=c.say('BLOCK')
    wait(lambda:any(e['tool']=='piper' for e in events()))
    c.play('hello.wav',interrupt=False);c.play('other.wav',interrupt=False)
    with pytest.raises(ValueError,match='full'):c.say('overflow',interrupt=False)
    with pytest.raises((ValueError,OSError)):c.play('../outside.wav')
    with pytest.raises(ValueError):c.say('bad',voice='../voice')
    with pytest.raises(ValueError):c.say('')
    with pytest.raises(ValueError):c.say('x'*4001)
    assert c.status()['job_id']==job and c.status()['busy']


def test_assets_reject_escape_and_incomplete_voice(rig,tmp_path):
    c,events,clips,voices,_=rig
    outside=tmp_path/'outside.wav';outside.write_bytes(b'outside')
    (clips/'escape.wav').symlink_to(outside)
    (voices/'incomplete.onnx').write_bytes(b'model')
    with pytest.raises(ValueError):c.play('escape.wav')
    clips_list,voice_list=c.list_assets()
    assert clips_list==['hello.wav','other.wav','vitals_measurement.wav']
    assert voice_list==['en_US-lessac-medium','en_US-ryan-medium']
    assert not events()


def test_failure_is_reported_and_worker_recovers(rig):
    c,events,_,_,binpath=rig
    converter=binpath/'sox';original=converter.read_text()
    converter.write_text('#!/bin/sh\necho broken-converter >&2\nexit 7\n')
    job=c.play('hello.wav')
    wait(lambda:c.status()['job_id']==job and c.status()['state']=='failed')
    assert 'broken-converter' in c.status()['last_error']
    converter.write_text(original)
    job=c.play('hello.wav')
    wait(lambda:c.status()['job_id']==job and c.status()['state']=='done')
    assert c.status()['last_error']==''


def test_processing_timeout_kills_child_and_recovers(rig):
    c,events,*_=rig
    c.timeout=.1
    job=c.say('BLOCK timeout')
    wait(lambda:c.status()['job_id']==job and c.status()['state']=='failed')
    assert 'timed out' in c.status()['last_error']
    for event in events():
        if event['tool']=='piper':assert not Path(f"/proc/{event['pid']}").exists()
    c.timeout=2.
    job=c.say('Next job')
    wait(lambda:c.status()['job_id']==job and c.status()['state']=='done')


def test_close_interrupts_active_loop(rig):
    c,events,*_=rig
    c.play('hello.wav',loop=True)
    wait(lambda:any(e['tool']=='aplay' for e in events()))
    start=time.monotonic();c.close()
    assert time.monotonic()-start<.6
    assert not c._worker.is_alive()


def test_bundled_announcement_plays_without_synthesis_and_can_be_overridden(rig):
    c, events, clips, *_ = rig
    job = c.play('vitals_measurement.wav')
    wait(lambda: c.status()['job_id'] == job and c.status()['state'] == 'done')
    assert not any(e['tool'] == 'piper' for e in events())
    assert any(e['tool'] == 'aplay' for e in events())
    assert 'robot_audio/clips/vitals_measurement.wav' in str(c._clip('vitals_measurement.wav'))
    (clips/'vitals_measurement.wav').write_bytes((clips/'hello.wav').read_bytes())
    assert c._clip('vitals_measurement.wav') == clips/'vitals_measurement.wav'
