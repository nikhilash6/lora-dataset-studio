import { useState } from 'react';
import { apiFetch } from '../../../api/fetchClient';
import InstallRunner from '../../setup/InstallRunner';
import { fmtSize } from '../../setup/fmtSize';
import { HelpBadge } from '../../../help/HelpMode';
import ComfyNodeRepair from '../../setup/ComfyNodeRepair';

/** Use only repairs the resolver says can satisfy the selected files. */
export default function StudioModelDownloads({ readiness, onRefresh }) {
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState('');
  const downloads = readiness?.downloads || [];
  const nodes = readiness?.missing_nodes || [];
  if (!downloads.length && !nodes.length) return null;

  const refresh = async () => {
    setChecking(true);
    setError('');
    try {
      // Invalidate discovery before reloading the run: an installed base has a
      // loader-relative subfolder, unlike its missing-file placeholder.
      await apiFetch('/api/comfy/trained-image-models?force=1');
      await onRefresh?.();
    } catch (e) {
      setError(e.message || 'Could not check the downloaded models. Try checking again.');
    } finally {
      setChecking(false);
    }
  };

  return (
    <>
    {nodes.length > 0 && <ComfyNodeRepair nodes={nodes} nodePacks={readiness.node_packs} onRefresh={onRefresh} />}
    {downloads.length > 0 && <section aria-label="Download missing models"
      className="min-w-0 space-y-3 rounded-lg border border-amber-400/40 bg-amber-400/5 p-3">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-content">Prepare {readiness.label} for generation<HelpBadge topic="studio_models" /></h3>
        <p className="text-xs text-content-muted">
          Download the required files here. LDS puts them in the correct folders and checks them
          when each download finishes. Your prompt and checkpoint selection stay in place.
        </p>
      </div>
      {downloads.map((item) => (
        <div key={item.action} className="min-w-0 space-y-1.5 border-t border-border pt-2">
          <p className="text-xs font-medium text-content">
            {item.label}{item.size_bytes > 0 ? ` · ${fmtSize(item.size_bytes)}` : ''}
          </p>
          <p className="break-all text-[0.6875rem] text-content-muted">{item.filename}</p>
          <InstallRunner action={item.action}
            buttonLabel={`${item.repair ? 'Repair' : 'Download'} ${item.label}`}
            onDone={refresh} />
          {item.source_url && <a href={item.source_url} target="_blank" rel="noreferrer"
            className="inline-block text-xs text-content-muted underline">Model details and license</a>}
        </div>
      ))}
      <button type="button" onClick={refresh} disabled={checking}
        className="min-h-10 rounded-md border border-border px-2.5 py-1.5 text-xs text-content disabled:opacity-50">
        {checking ? 'Checking model files…' : 'Check installed files again'}
      </button>
      {error && <p role="alert" className="text-xs text-red-400">{error}</p>}
    </section>}
    </>
  );
}
