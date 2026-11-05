import io
import types
import unittest
from test_security import m


class MobileViewport(unittest.TestCase):
    def output(self,agent):
        fake=types.SimpleNamespace(headers={'User-Agent':agent},wfile=io.BytesIO(),
             send_response=lambda *a:None,send_header=lambda *a:None,end_headers=lambda:None)
        m.Handler.output(fake,'<p>Fixture</p>')
        return fake.wfile.getvalue().decode()

    def test_phone_native_viewport_is_fixed_and_ios_controls_have_own_space(self):
        for platform in ('iOS','Android'):
            page=self.output('Mozilla/5.0 DubsNative/1.2 '+platform)
            self.assertIn('dubs-native-mobile',page.split('<meta charset')[0])
            self.assertIn('initial-scale=1,maximum-scale=1,minimum-scale=1,user-scalable=no',page)
            self.assertEqual('dubs-native-ios' in page.split('<meta charset')[0],platform=='iOS')

    def test_browser_and_tv_keep_normal_zoom_and_no_native_overlay_padding(self):
        for agent in ('Mozilla/5.0 Mobile Safari','DubsNative/1.2 Android DubsTV/1.0'):
            page=self.output(agent)
            self.assertIn('<html lang="ru" class="">',page)
            viewport=page.split('name="viewport" content="',1)[1].split('"',1)[0]
            self.assertEqual(viewport,'width=device-width,initial-scale=1')


if __name__=='__main__':unittest.main()
