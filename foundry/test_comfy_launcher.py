"""The real launcher must be recognizable without weakening GPU patrol."""
from pathlib import Path
import unittest
from test_foundry import F


class LauncherOwnership(unittest.TestCase):
    def test_launched_command_is_factory_owned(self):
        lines = Path(__file__).with_name('comfy_launch.bat').read_text().splitlines()
        command = next(l for l in lines if l.startswith('"%PYTHON_DIR%\\python.exe"'))
        command = command.replace('%PYTHON_DIR%', r'F:\comfyui-rocm\python_env')
        proc = {'pid': 19288, 'ppid': 100, 'cmd': command}
        self.assertEqual(F.gpu_owner(proc, own_pid=99999, claim={})[0], 'factory')

    def test_relative_or_foreign_main_stays_unregistered(self):
        for command in (r'python_env\python.exe -u main.py --port 8189',
                        r'C:\unrelated\python.exe C:\unrelated\main.py --port 8189'):
            proc = {'pid': 19288, 'ppid': 100, 'cmd': command}
            self.assertIsNone(F.gpu_owner(proc, own_pid=99999, claim={})[0])


if __name__ == '__main__':
    unittest.main()
