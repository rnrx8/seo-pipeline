import re
import unittest
from unittest.mock import patch
from pipeline.step_outline import ensure_complete_volume_design

class VolumeWarnings(unittest.TestCase):
    def test_dense_outline_is_retained_within_requested_length(self):
        text='## H2: 悩みを整理する\n'+''.join(f'### H3: 視点{i}\n' for i in range(30))+'## H2: まとめ\n'
        with patch('pipeline.quality_warnings.warn') as warn:
            result, repaired=ensure_complete_volume_design(text,'5,000字')
        warn.assert_called_once()
        self.assertTrue(repaired)
        self.assertEqual(len(re.findall(r'^#### H3：視点\d+$', result, re.M)),30)
        allocations=[int(v.replace(',','')) for v in re.findall(r'\| ([\d,]+)字 \|',result)]
        self.assertEqual(sum(allocations),5000)
        self.assertTrue(all(v>0 for v in allocations))

if __name__=='__main__':unittest.main()
