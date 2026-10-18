"""Publish LoRA checkpoints and their generated images through owned plugin surfaces."""


def _stamp_lineage(checkpoints, record_id):
    """``lineage.checkpoints`` filter: ``ck['civitai'] = link`` for every save
    of the run that has a page, keyed by the FILE (the numbered save and the
    final of a run that ended on it share a step). Best-effort like the notes:
    a pill only GAINS a badge here, a failure in the link store must never
    blank a node of the tree."""
    from .publish import links_for_record
    try:
        links = links_for_record(record_id)
    except Exception:  # noqa: BLE001 — the tree renders without the badges
        return checkpoints
    for ck in checkpoints or []:
        link = links.get(ck.get('filename') or '')
        if link:
            ck['civitai'] = link
    return checkpoints


def _count_links_for_summary(summary, rec):
    """``training_run.delete_summary`` filter: the links this deletion will
    DETACH (the page stays on the site and stays offered to the dataset's
    pictures). Additive — the dialog ignores what it does not know."""
    try:
        from .models import CivitaiLink
        summary['civitai_links_detached'] = int(CivitaiLink.query.filter_by(record_id=rec.id).count())
    except Exception:  # noqa: BLE001
        summary['civitai_links_detached'] = 0
    return summary


def _detach_links_of_run(rec):
    """``training_run.delete`` hook: the links survive their run the way the
    generated images do — they only lose the record (see models.CivitaiLink)."""
    from .publish import detach_links_of_run
    detach_links_of_run(rec.id)


def _drop_links_of_dataset(dataset_id):
    """``dataset.delete`` hook, inside the dataset's own transaction: nothing is
    left to post under the pages once the dataset's pictures are gone (the
    pages on the site are untouched)."""
    from .models import db
    from .models import CivitaiLink
    for link in CivitaiLink.query.filter_by(dataset_id=dataset_id).all():
        db.session.delete(link)


def register(ctx):
    from . import publish
    from .probes import configured
    from .routes import bp
    # The generation-model hash cache lives with the plugin's own state.
    publish.set_data_dir(getattr(ctx, 'data_dir', None))
    ctx.register_blueprint(bp, url_prefix='/api/civitai')
    ctx.register_probe('civitai', configured)
    ctx.register_hook('lineage.checkpoints', _stamp_lineage)
    ctx.register_hook('training_run.delete_summary', _count_links_for_summary)
    ctx.register_hook('training_run.delete', _detach_links_of_run)
    ctx.register_hook('dataset.delete', _drop_links_of_dataset)
