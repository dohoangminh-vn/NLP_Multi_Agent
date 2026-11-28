import json
import pytest
from src.utils.yccd_adapter import read_yccd, load_yccd
from src.utils.pedagogy_validator import PedagogyValidationError


def write(tmp_path, data):
    p = tmp_path / 'taxonomy.json'
    p.write_text(json.dumps(data), encoding='utf-8-sig')
    return p


def item(bai=20, **kw):
    return dict(yccd_id=f'F10.B{bai}.YC1', description='Mục tiêu mẫu', competency_codes=['NLc'], bloom_levels=['Hiểu'], **{}) | kw


def hierarchical(*items):
    return {'lessons':[{'bai_so':20, 'yccd_items':list(items)}]}


def test_hierarchical(tmp_path):
    rows,status=read_yccd(20,write(tmp_path,hierarchical(item())))
    assert status=='mapped_from_tv2' and rows[0]['code']==rows[0]['yccd_id']
    assert rows[0]['text']==rows[0]['description'] and rows[0]['competency_codes']==['NLc']


def test_legacy(tmp_path):
    rows,status=read_yccd(20,write(tmp_path,{'F10.B20.YC1':{'bai_so':20,'text':'Mục tiêu SGK','source':'SGK'}}))
    assert status=='legacy_sgk_unverified' and rows[0]['competency_codes']==[] and rows[0]['source']=='SGK'


@pytest.mark.parametrize('changes',[{'yccd_id':'F10.B21.YC1'},{'description':''},{'competency_codes':[]},{'competency_codes':['bad']},{'bloom_levels':'Hiểu'}])
def test_invalid(tmp_path,changes):
    with pytest.raises(PedagogyValidationError): read_yccd(20,write(tmp_path,hierarchical(item(**changes))))


def test_duplicate(tmp_path):
    with pytest.raises(PedagogyValidationError): read_yccd(20,write(tmp_path,hierarchical(item(),item())))


def test_missing(tmp_path):
    assert read_yccd(20,tmp_path/'none')==([], 'missing_taxonomy')
    with pytest.raises(PedagogyValidationError): load_yccd(20,tmp_path/'none')


def test_no_future_lesson(tmp_path):
    p=write(tmp_path,{'lessons':[{'bai_so':21,'yccd_items':[item(21)]}]})
    assert read_yccd(20,p)==([], 'lesson_not_unique_or_missing')


def test_bad_json(tmp_path):
    p=tmp_path/'bad.json'; p.write_text('{')
    with pytest.raises(PedagogyValidationError): read_yccd(20,p)


def test_legacy_mismatch(tmp_path):
    with pytest.raises(PedagogyValidationError): read_yccd(20,write(tmp_path,{'F10.B20.YC1':{'bai_so':21,'text':'x'}}))
