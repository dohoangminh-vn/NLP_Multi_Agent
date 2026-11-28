import copy
import json
import pytest
from src.utils.revision_engine import parse_instruction, apply_patch
from src.utils.pedagogy_validator import PedagogyValidationError
from test_detail_designer import design, Fake


def instruction(*paths):
    return json.dumps({'targets':list(paths),'instructions':'Sửa đúng mục lỗi'})


def test_preserve():
    old=design(); original=copy.deepcopy(old)
    path='activities.2.expected_product'
    new=design(Fake(json.dumps({'replacements':{path:'Bảng kết quả'}})),previous=old,revision=instruction(path))
    assert old==original
    original['activities'][2]['expected_product']='Bảng kết quả'
    assert new==original


@pytest.mark.parametrize('path',['knowledge_goals','activities.0.duration_minutes','activities.0.activity_name','worksheets.0.tier','worksheets.1.bloom_level','activities.4.content','worksheets.3.solution_code','__class__'])
def test_protected(path):
    with pytest.raises(PedagogyValidationError): parse_instruction(instruction(path))


def test_duplicate():
    with pytest.raises(PedagogyValidationError): parse_instruction(instruction('qualities','qualities'))


@pytest.mark.parametrize('replacements',[{}, {'qualities':'new','teaching_materials':'extra'}, {'qualities':''}])
def test_invalid_patch(replacements):
    old=design(); before=copy.deepcopy(old)
    with pytest.raises(PedagogyValidationError): design(Fake(json.dumps({'replacements':replacements})),previous=old,revision=instruction('qualities'))
    assert old==before


def test_noop():
    old=design()
    with pytest.raises(PedagogyValidationError): apply_patch(old,['qualities'],{'replacements':{'qualities':old['qualities']}})


def test_fake_citation():
    old=design(); c=copy.deepcopy(old['activities'][0]['citations']); c[0]['page_number']=999
    with pytest.raises(PedagogyValidationError): design(Fake(json.dumps({'replacements':{'activities.0.citations':c}})),previous=old,revision=instruction('activities.0.citations'))


def test_retry():
    old=design(); f=Fake('invalid',json.dumps({'replacements':{'qualities':'Trách nhiệm'}}))
    assert design(f,previous=old,revision=instruction('qualities'))['qualities']=='Trách nhiệm'
    assert f.calls==2


def test_missing_previous():
    with pytest.raises(PedagogyValidationError): design(Fake('{}'),revision=instruction('qualities'))


def test_no_llm():
    with pytest.raises(PedagogyValidationError): design(previous=design(),revision=instruction('qualities'))
