import copy,unittest
from pipeline.scoped_edit_correction import delta,compose
from pipeline.content_quality import ContentQualityError

class ScopedEditCorrectionTests(unittest.TestCase):
    def fixture(self):
        a={'reader_cases':{'cases':[{'id':'a','text':'same'},{'id':'b','text':'old'}]},
            'heading_answer_entries':[{'id':'h','opening_quote':'old'}]}
        b=copy.deepcopy(a);b['reader_cases']['cases'][1]['text']='changed';b['heading_answer_entries'][0]['opening_quote']='new'
        return a,b

    def test_only_identical_actual_cases_inherit_previous_individual_review(self):
        old,new=self.fixture();v=delta(old,new)
        self.assertEqual([c['id'] for c in v['reader_cases']['cases']],['b'])
        previous={'reader_journeys':{'a':{'source':'first actual case'},'b':{'source':'old b'}},'heading_answer_reviews':{'h':{'source':'old h'}}}
        review={'checks':{'source':'new delta'},'reader_journeys':{'b':{'source':'new b'}},'heading_answer_reviews':{'h':{'source':'new h'}}}
        merged=compose(previous,old,review,v,new)
        self.assertEqual(merged['reader_journeys']['a'],previous['reader_journeys']['a'])
        self.assertEqual(merged['reader_journeys']['b'],review['reader_journeys']['b'])
        self.assertEqual(merged['heading_answer_reviews']['h'],review['heading_answer_reviews']['h'])

    def test_a_changed_case_cannot_be_removed_from_delta_to_reuse_its_old_pass(self):
        old,new=self.fixture();v=delta(old,new);v['reader_cases']['cases']=[]
        previous={'reader_journeys':{'a':{},'b':{}},'heading_answer_reviews':{'h':{}}}
        review={'reader_journeys':{},'heading_answer_reviews':{'h':{}}}
        with self.assertRaises(ContentQualityError):compose(previous,old,review,v,new)
