import json
from unittest.mock import patch
import pytest
from shortpost_worker.gpu_compute import validate_result,run

def test_gpu_output_requires_finite_word_timestamps():
    row={'language':'ru','text':'текст','segments':[{'start':0,'end':1,'text':'текст','words':[{'start':0,'end':1,'word':'текст'}]}]}
    assert validate_result(row,2)==row
    for value in (-1,float('nan'),30):
        bad=json.loads(json.dumps(row));bad['segments'][0]['words'][0]['end']=value
        with pytest.raises(ValueError):validate_result(bad,2)

def test_compute_refuses_path_traversal_before_file_access(tmp_path):
    with pytest.raises(ValueError,match='Invalid task token'):run(tmp_path,'../../other-file')

def test_compute_cannot_run_a_publication_operation(tmp_path):
    token='a'*12+'-0001';(tmp_path/(token+'.request.json')).write_text(json.dumps({'operation':'publish','model':'base'}))
    with pytest.raises(ValueError,match='Unsupported compute operation'):run(tmp_path,token)
