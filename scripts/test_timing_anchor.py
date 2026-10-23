import unittest
from timing_anchor import locate_boundary
def words(text):return [dict(word=w,start=i*.5,end=i*.5+.4) for i,w in enumerate(text.split())]
class BoundaryTests(unittest.TestCase):
 def test_repeated_anchor_refused(self):
  s='Maybe Before he starts Before it gets out';w=words(s)
  with self.assertRaises(ValueError):locate_boundary(s,w,'Before')
  o=locate_boundary(s,w,'Before it gets out');self.assertEqual(o['word_index'],4);self.assertEqual(o['source_after'],'Before it gets out')
 def test_explicit_prior_boundary(self):
  s='Maybe Before he starts Before it gets out';o=locate_boundary(s,words(s),'Before',after_word=1,after_char=6);self.assertEqual(o['word_index'],4)
 def test_documented_spelling_mapping(self):
  s='Recently Dark versus Rogue or Serral versus Reynor';w=words('Recently Dark versus Rogue or Seraph versus Raynor')
  with self.assertRaises(ValueError):locate_boundary(s,w,'or Serral')
  o=locate_boundary(s,w,'or Serral','or Seraph',offset_ms=595000);self.assertEqual(o['start_ms'],597000);self.assertTrue(o['explicit_spelling_mapping'])
 def test_hyphen_space(self):
  o=locate_boundary('Then Auto-turrets move',words('Then auto turrets move'),'Auto turrets');self.assertEqual(o['source_char_index'],5)
 def test_contraction(self):
  self.assertEqual(locate_boundary("Fine I don't know",words("Fine I don't know"),'I dont')['word_index'],1)
 def test_not_substring_of_larger_word(self):
  with self.assertRaises(ValueError):locate_boundary('Now before we start',words('Now before we start'),'fore')
 def test_missing_time(self):
  w=words('Now move on');w[1].pop('start')
  with self.assertRaises(ValueError):locate_boundary('Now move on',w,'move')
 def test_ambiguous_cleaned_source(self):
  with self.assertRaises(ValueError):locate_boundary('Then move and move',words('Then move on'),'move')
if __name__=='__main__':unittest.main()

