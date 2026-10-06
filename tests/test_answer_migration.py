import copy
import unittest
from pipeline.answer_migration import fingerprint,migration_candidate

class AnswerMigrationTests(unittest.TestCase):
    def setUp(self):
        self.matrix={'valid':True,'policy_sha256':'old','items':[{'id':'q1','answer':'男性3ヶ月、総額900円。掲載元2件を照合。','basis':'historical','status':'confirmed','supports_current_conclusion':False,'evidence':[{'url':'https://example.com','quote':'男性3ヶ月、総額900円。'}]}]}
        self.parts={'q1':[{'role':'answer','text':'男性3ヶ月、総額900円。'},{'role':'research_record','text':'掲載元2件を照合。'}]}

    def test_preserves_original_evidence_and_past_only_restriction(self):
        original=copy.deepcopy(self.matrix)
        value=migration_candidate(self.matrix,self.parts,expected_fingerprint=fingerprint(self.matrix))
        self.assertEqual(self.matrix,original)
        self.assertEqual(value['records'][0]['evidence'],original['items'][0]['evidence'])
        self.assertFalse(value['records'][0]['supports_current_conclusion'])
        self.assertFalse(value['production_approved'])
        self.assertNotIn('valid',value)
        self.assertEqual(value['source_policy_sha256'],'old')

    def test_rejects_silent_loss_invention_or_reordering(self):
        for text in ('総額900円。','女性3ヶ月、総額900円。掲載元2件を照合。','掲載元2件を照合。男性3ヶ月、総額900円。'):
            with self.assertRaises(ValueError):migration_candidate(self.matrix,{'q1':[{'role':'answer','text':text}]},expected_fingerprint=fingerprint(self.matrix))

    def test_rejects_changed_source_and_unknown_id(self):
        with self.assertRaises(ValueError):migration_candidate(self.matrix,self.parts,expected_fingerprint='stale')
        with self.assertRaises(ValueError):migration_candidate(self.matrix,{'missing':[]},expected_fingerprint=fingerprint(self.matrix))

    def test_unchanged_answers_are_verbatim_and_no_keyword_is_banned(self):
        self.matrix['items'][0]['answer']='第三者認証と2025年の適用条件。'
        value=migration_candidate(self.matrix,{},expected_fingerprint=fingerprint(self.matrix))
        self.assertEqual(value['records'][0]['segments'],[{'role':'answer','text':'第三者認証と2025年の適用条件。'}])
