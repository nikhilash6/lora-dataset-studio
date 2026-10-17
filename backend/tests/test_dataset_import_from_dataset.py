"""Import from a dataset: only the images picked in another dataset are copied,
with their caption, its writer stamp and their framing; near-duplicates of what
the target already holds are skipped and the source is left as it was."""
import io
import random

from PIL import Image


def _noise_png(seed):
    """Random pixels, so two seeds never pass the perceptual dedup as one image."""
    rng = random.Random(seed)
    image = Image.new('RGB', (96, 64))
    image.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                   for _ in range(96 * 64)])
    out = io.BytesIO()
    image.save(out, 'PNG')
    return out.getvalue()


def test_copies_only_the_picked_images_with_their_caption(app, client):
    from app.config import LOCAL_USER
    from app.models import FaceDatasetImage
    from app.services import face_dataset_service as svc

    with app.app_context():
        source = svc.create_dataset(LOCAL_USER, 'Source', 'source_tok')
        target = svc.create_dataset(LOCAL_USER, 'Target', 'target_tok')
        source_ids, failed = svc.import_images(
            LOCAL_USER, source.id, [_noise_png(1), _noise_png(2), _noise_png(3)])
        assert failed == 0 and len(source_ids) == 3
        first = svc.db.session.get(FaceDatasetImage, source_ids[0])
        first.caption = 'a woman reading by a window'
        first.caption_origin = 'asserted'
        first.framing = 'bust'
        svc.db.session.commit()
        # The third picked image is already in the target: it must be skipped.
        svc.import_images(LOCAL_USER, target.id, [_noise_png(3)])
        source_id, target_id = source.id, target.id

    r = client.post(f'/api/dataset/{target_id}/import-from-dataset', json={
        'source_dataset_id': source_id,
        'image_ids': [source_ids[0], source_ids[2], 999999]})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert (body['imported'], body['duplicates'], body['failed']) == (1, 1, 1)

    with app.app_context():
        rows = (FaceDatasetImage.query.filter_by(dataset_id=target_id)
                .order_by(FaceDatasetImage.id).all())
        assert len(rows) == 2
        copied = rows[-1]
        assert copied.status == 'keep'
        assert copied.caption == 'a woman reading by a window'
        assert copied.caption_origin == 'asserted'
        assert copied.framing == 'bust'
        assert FaceDatasetImage.query.filter_by(dataset_id=source_id).count() == 3


def test_refuses_the_dataset_itself_as_its_source(app, client):
    from app.config import LOCAL_USER
    from app.services import face_dataset_service as svc

    with app.app_context():
        dataset = svc.create_dataset(LOCAL_USER, 'Alone', 'alone_tok')
        ids, _failed = svc.import_images(LOCAL_USER, dataset.id, [_noise_png(4)])
        dataset_id = dataset.id

    r = client.post(f'/api/dataset/{dataset_id}/import-from-dataset',
                    json={'source_dataset_id': dataset_id, 'image_ids': ids})
    assert r.status_code == 400
