from remote_yam.public_conversation import project_events, response_text, request_images
from remote_yam.robocurve_policy import CallRecorder
import base64


def test_only_conversation_and_exact_image_blob(tmp_path):
    recorder = CallRecorder(tmp_path)
    content = [{'type':'input_text','text':"camera 'top_cam' (step 1):"},
               {'type':'input_image','image_url':'data:image/jpeg;base64,'+base64.b64encode(b'original-image').decode()}]
    images = request_images(content, recorder)
    assert (recorder.path/'blobs'/images[0]['digest']).read_bytes() == b'original-image'
    events = [{'id':1,'timestamp':1,'kind':'model_request','message':'internal',
               'details':{'prompt':'Pick up the block','images':images,'secret':'never-share'}},
              {'id':2,'timestamp':2,'kind':'packet_progress','message':'noise'}]
    public = project_events(events, recorder.path.name)
    assert len(public) == 1
    assert public[0]['message'] == 'Pick up the block'
    assert public[0]['images'][0]['url'].endswith(images[0]['digest'])
    assert 'never-share' not in str(public)


def test_response_text_and_tool_note():
    assert response_text({'output':[{'type':'message','content':[{'type':'output_text','text':'I see the block.'}]},
        {'type':'function_call','name':'move','arguments':'{"note":"Moving closer"}'}]}) == 'I see the block.\n\nmove: {"note":"Moving closer"}'


def test_request_is_copied_and_full_arguments_preserved(tmp_path):
    from remote_yam.public_conversation import request_display, request_text
    import copy
    payload = {'model':'test','input':[{'role':'system','content':'Full intro rules'},
        {'role':'user','content':'Goal: move'}, {'type':'reasoning','encrypted_content':'opaque'},
        {'role':'user','content':[{'type':'input_text','text':'Measured joints: [1,2,3]'}]}],
        'tools':[{'name':'move','parameters':{'type':'object'}}]}
    original = copy.deepcopy(payload)
    display = request_display(payload, CallRecorder(tmp_path))
    assert payload == original
    assert display['tools'] == payload['tools']
    assert 'Full intro rules' in request_text(payload)
    assert 'Measured joints: [1,2,3]' in request_text(payload)
    assert 'opaque' not in str(display)
    assert response_text({'output':[{'type':'function_call','name':'move',
        'arguments':'{"note":"Go","joints":[1,2,3]}'}]}) == 'move: {"note":"Go","joints":[1,2,3]}'


def test_makermods_preview_contains_each_sent_camera(tmp_path):
    recorder = CallRecorder(tmp_path)
    names = ['overhead', 'left', 'right']
    content = []
    for name in names:
        content.extend([{'type':'input_text', 'text':f"camera '{name}_cam' (step 0):"},
                        {'type':'input_image', 'image_url':'data:image/jpeg;base64,'+base64.b64encode(name.encode()).decode()}])
    images = request_images(content, recorder)
    public = project_events([{'kind':'model_request', 'details':{'images':images}}], recorder.path.name)
    assert [image['name'] for image in public[0]['images']] == names
    for name, image in zip(names, images):
        assert (recorder.path/'blobs'/image['digest']).read_bytes() == name.encode()


def test_single_so101_front_preview_and_invalid_digest(tmp_path):
    recorder = CallRecorder(tmp_path)
    images = request_images([{'type':'input_text','text':"camera 'front_cam' (step 0):"},
        {'type':'input_image','image_url':'data:image/jpeg;base64,'+base64.b64encode(b'front').decode()}], recorder)
    images.append({'name':'overhead', 'digest':'../not-an-image'})
    public = project_events([{'kind':'model_request', 'details':{'images':images}}], recorder.path.name)
    assert [image['name'] for image in public[0]['images']] == ['front']
