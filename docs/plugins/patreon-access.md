# Patreon access

New features, exclusive plugins and LDS updates are reserved for Patreon
members (LDS Member and LDS Pro). The installed version and installed plugins
keep working when a membership ends; new downloads and updates need an active
membership. LDS Pro adds custom plugin requests, handled outside the app.

The app never sees Patreon credentials. **Connect Patreon** asks the operator's
service for a one-time authorization link on `www.patreon.com`; the browser
approves it there and the service records the link. Only the service's library
answer counts as membership, and the service checks it again for every download.

## Shipped configuration

`store/patreon.json` ships with the app:

```json
{
  "format": "lds-patreon/1",
  "service_url": "https://store.example.invalid",
  "checkout_origin": "https://www.patreon.com",
  "join_url": "https://www.patreon.com/c/Loraperfectgf",
  "catalog": {
    "root": null,
    "first_party_ids": [],
    "plugin_ids": [],
    "plugin_namespaces": ["patreon"]
  }
}
```

- `service_url` is an origin without a path. HTTPS is required; plain HTTP is
  accepted only for a literal loopback address (`127.0.0.1`, `::1`). The
  reserved `.invalid` placeholder keeps Patreon access switched off.
- `checkout_origin` must be `https://www.patreon.com`. `join_url`, shown before
  the installation is connected, must be an HTTPS page on `www.patreon.com`.
- `catalog.root` embeds the independently verified TUF bootstrap root of the
  Patreon catalog, so the file ships alone in the Windows ZIP and both Docker
  images. `root_path` (relative to the file) is accepted instead, for test
  instances. Without a root the Patreon catalog and core channel stay off.
- The Patreon catalog provides the identifiers it lists, `first_party_ids`
  (LDS identifiers) and `plugin_ids` (`publisher.plugin`), and every external
  identifier whose publisher part is in `plugin_namespaces`. A members-only
  plugin published later as `patreon.<name>` therefore appears in every
  installation without a core update. A namespace uses the publisher grammar
  of plugin identifiers (2-32 lowercase letters, digits or `_`, starting with
  a letter); `lds` and Windows device names are refused.
- A namespace never grants an LDS identifier: those stay explicit
  `first_party_ids`, and a product of the public catalog cannot be listed.
- An identifier also listed by a local [private catalog](private-catalogs.md)
  stays with that source, inside a namespace too. No other source serves an
  identifier of these namespaces: not the public catalog, and not a private
  catalog that does not list it.

`LDS_STORE_PATREON_CONFIG` points a test instance at another file with the
same format. The TUF repository is read from `<service_url>/tuf/metadata/`
and `<service_url>/tuf/targets/`.

## Plugins

Patreon products appear in the Store marked **For Patreon members**. Their
releases must be `{"kind": "paid", "product": …, "provider": "patreon"}`, and
only the Patreon catalog may use that label. Installation and updates go
through the usual review; the archive comes through a download grant from the
Patreon service, checked against the signed length and SHA-256. Without an
active membership the review offers Connect Patreon and Join on Patreon.

## Core updates

The Patreon catalog signs `core-releases.json` (`lds-core-releases/1`). Each
release names its `commit`, its public `base`, a Git `bundle` of `base..commit`
whose only ref is `refs/heads/patreon`, and a ZIP `archive`. Versions follow
the app's `YYYY.MM.DD[.N]` scheme. Lengths and hashes come only from the TUF
target metadata; bytes come only through grants for `lds_core`.

| Installation | Behavior |
| --- | --- |
| Git checkout | Downloads the verified bundle, reads it into a private ref, then fast-forwards only. Local commits, modified tracked files or a diverged history refuse, like `git pull --ff-only`. LDS restarts itself. |
| Pinokio | Same fast-forward. Pinokio keeps the restart: Stop, Update (installs changed requirements), Start. Until then LDS accepts no new work. |
| ZIP | Downloads the verified archive and applies it with the existing staged swap, backup and rollback, then restarts. |
| Docker | Not applied: see the limit below. The installed version keeps working. |

No update is applied while generation, training, captioning, a bank or dataset
operation, an installation or another restart is in progress: the same idle
check as plugin **Apply** runs before the first file changes, and new work stays
refused until LDS restarts.

A fast-forwarded checkout records the release in `refs/lds/patreon/installed`.
While HEAD contains that commit and the public branch does not, the public
update check reports no update and the public updater never resets the
checkout onto the public branch, even when a public commit shares its tree.
Pinokio's Update tab tolerates that state instead of failing.

### Operator requirements

- Keep the Patreon line fast-forwardable: merge public commits into it rather
  than rebasing it. A checkout that took a public commit absent from the next
  release's history cannot fast-forward to it; set `base` to the latest public
  commit the release contains.
- Item statuses in the library keep the existing values (`active`, `revoked`,
  `verification_required`); an inactive membership must not use another value.

### Docker limit

A container cannot replace its own image, and the host update script does not
apply Patreon releases yet. Two gaps remain before it can:

1. The host must verify the bytes itself. Applying an archive that the container
   wrote to its data folder would let anything running in the container, plugins
   and ComfyUI custom nodes included, run code on the host at the next update.
   Proposed: the host updater reads `service_url` from its own checkout, checks
   the staged archive against the service's target metadata over TLS (the same
   trust it gives GitHub today), then uses the existing ZIP transaction.
2. Docker installations are full source trees, and the host updater requires
   the Docker files. The catalog needs a full-tree archive for them, separate
   from the Windows ZIP layout.
