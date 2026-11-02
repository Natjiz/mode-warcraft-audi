from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
from companion.warcraft_audi.capture import AutoLocator
from companion.warcraft_audi.protocol import Assembler, WIDTH, HEIGHT, find_geometry, frame_packet, render_frame, read_frame


class Display:
    def __init__(self):
        self.monitors = [dict(left=-1600, top=0, width=3520, height=1080),
                         dict(left=0, top=0, width=1920, height=1080),
                         dict(left=-1600, top=0, width=1600, height=900)]
        self.images = {0: Image.new('RGB', (1920,1080), '#4b3420'), -1600: Image.new('RGB', (1600,900), '#213542')}
    def grab(self, region):
        for monitor in self.monitors[1:]:
            if monitor['left'] <= region['left'] < monitor['left'] + monitor['width']:
                x, y = region['left'] - monitor['left'], region['top'] - monitor['top']
                image = self.images[monitor['left']].crop((x,y,x+region['width'],y+region['height']))
                return SimpleNamespace(size=image.size, rgb=image.tobytes())
        raise ValueError('monitor removed')


class AutomaticDesktopTests(unittest.TestCase):
    def test_discovers_multi_page_quest_on_second_monitor_and_recovers_after_move_scale(self):
        message = {'v':1,'action':'play','quest_id':48313,'text':'Текст Киро. '*110}
        data = json.dumps(message,ensure_ascii=False).encode()
        display, locator, assembler = Display(), AutoLocator(), Assembler()
        received = None
        for page in range((len(data)+231)//232):
            packet = frame_packet(data,123,7,page)
            # First frames are on a negative-coordinate monitor. Then resolution,
            # position and scaling change without any click or saved region.
            display.images = {0:Image.new('RGB',(1920,1080),'#4b3420'), -1600:Image.new('RGB',(1600,900),'#213542')}
            if page < 2:
                target, pos, scale = -1600,(350,400),0.75
            else:
                target, pos, scale = 0,(1400,730),1.5
            image=render_frame(packet).resize((round(WIDTH*scale),round(HEIGHT*scale)),Image.Resampling.BILINEAR)
            display.images[target].paste(image,pos)
            found=locator.read(display,now=page+1)
            self.assertEqual(found,packet)
            received=assembler.feed(found) or received
        self.assertEqual(received,message)

    def test_false_marker_triplet_and_corrupt_frame_do_not_hide_real_signal(self):
        packet=frame_packet(b'{"v":1,"action":"stop"}',3,1,0)
        canvas=Image.new('RGB',(1920,1080),'#3e2c1c')
        false=render_frame(bytes(256))
        canvas.paste(false,(10,10))
        ImageDraw.Draw(canvas).rectangle((400,100,1800,600),fill='cyan')
        canvas.paste(render_frame(packet),(1100,700))
        self.assertEqual(read_frame(canvas,find_geometry(canvas)),packet)
        blank=Image.new('RGB',(1920,1080),'magenta')
        with self.assertRaises(ValueError):find_geometry(blank)

    def test_no_calibration_auto_start_and_old_region_is_ignored(self):
        from companion.warcraft_audi.app import DesktopApp
        class Player:
            volume=1
            def stop(self):pass
        with tempfile.TemporaryDirectory() as directory, patch('companion.warcraft_audi.app.ScreenWatch') as Watch:
            Path(directory,'settings.json').write_text(json.dumps({'region':{'left':9000},'geometry':[1,2,3,4],'volume':45}))
            app=DesktopApp(Path(directory),Player())
            try:
                Watch.return_value.start.assert_called_once()
                self.assertNotIn('region',app.config)
                self.assertNotIn('geometry',app.config)
                self.assertEqual(app.controller.player.volume,0.45)
            finally:app.close()

    def test_settings_resume_automatically_but_preserve_explicit_pause(self):
        from companion.warcraft_audi.app import DesktopApp
        class Player:
            volume=1
            def stop(self):pass
        with tempfile.TemporaryDirectory() as directory, patch('mss.mss', side_effect=OSError('test screen unavailable')):
            app=DesktopApp(Path(directory),Player())
            try:
                app.command('settings',{'cache_mb':64})
                self.assertTrue(app.watch.running.is_set())
                app.command('pause')
                app.command('settings',{'cache_mb':128})
                self.assertFalse(app.watch.running.is_set())
                self.assertEqual(app.config['cache_mb'],128)
            finally:app.close()

    def test_game_can_appear_after_program_and_occlusion_stops_audio(self):
        from companion.warcraft_audi.app import DesktopApp
        display=Display()
        class Screen:
            def __enter__(self):return display
            def __exit__(self,*args):pass
        class Player:
            volume=1
            def stop(self):pass
        with tempfile.TemporaryDirectory() as directory, patch('mss.mss',Screen):
            app=DesktopApp(Path(directory),Player(),auto_start=False)
            try:
                with patch.object(app.controller,'handle') as handle:
                    app.command('start')
                    time.sleep(.15)
                    handle.assert_not_called()
                    packet=frame_packet(b'{"v":1,"action":"stop"}',5,7,0)
                    display.images[-1600].paste(render_frame(packet),(250,230))
                    deadline=time.monotonic()+4
                    while not handle.called and time.monotonic()<deadline:time.sleep(.02)
                    self.assertTrue(handle.called)
                    self.assertEqual(app.watch.connection,'connected')
                    display.images[-1600]=Image.new('RGB',(1600,900),'#213542')
                    deadline=time.monotonic()+4
                    while app.watch.connection!='waiting' and time.monotonic()<deadline:time.sleep(.02)
                    self.assertEqual(app.watch.connection,'waiting')
            finally:app.close()


if __name__=='__main__':unittest.main()
