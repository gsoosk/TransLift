# apache_roller-0001 — systematic problems

Reported by the reviewer of batch apache_roller-0001 in its hand-back (it did not create this
file itself, judging these to be extensions of patterns already in reviews/apache_roller/calibration.md).
Recorded here by the orchestrator so they survive the session. txscan at the time: 55c84539a215.
Batch outcome: 17/17 valid, 6 confirmed, 11 corrected.

- **A second write after the method's own `flush()`**, beyond the "flush() then strategy.refresh()"
  pattern calibration lists for 6 sites: `createMediaFile` (JPAMediaFileManagerImpl.java:220),
  `createThemeMediaFile` (:242) and `updateMediaFile` (:297, :314) each call
  `roller.getWeblogManager().saveWeblog(weblog)` after their own `roller.flush()`. That persist
  belongs to a new, uncommitted unit, not the one the extractor listed. The Weblog writes were
  removed from each verdict.
- **Region-in-region nesting is not a boundary.** Roots that call other catalogued region roots
  (`upgradeFileStorage` -> `upgradeUploadsDir` -> `createMediaFile`/`updateThumbnail`) are listed with
  the nested regions' operations although those regions flush independently. Correcting this collapses
  large parent transactions (`WebloggerImpl.initialize`, `upgradeFileStorage`, `upgradeUploadsDir`,
  `removeWeblogContents`) to the 1-2 operations that genuinely run in their own unit, out of a dozen or
  more listed. This is the main driver of the batch's 11 corrections and is the same class of problem as
  the implicit-twin bug fixed in round 1, one level down: an explicit region's walk does not stop at a
  nested explicit region root.
- **Multiple flush() points in one method.** `JPAWeblogManagerImpl.removeWeblogContents` flushes at
  lines 138, 147 and 161 and was split into three units, the same split-boundary issue calibration
  noted for `DatabaseInstaller.upgradeTo400`. Splitting it exposed a genuine extractor miss:
  `removeWeblogEntryTag`'s own `strategy.remove(tag)` at JPAWeblogEntryManagerImpl.java:517 carries no
  DB marker; it was added with `ops_add`.
- **Dynamic-JPQL misattribution.** `JPAWeblogEntryManagerImpl.getTags` (:1093) queries
  `FROM WeblogEntryTagAggregate`, but op 9 of 931454b97bbb was labelled `Weblog`/`weblog`; corrected to
  `WeblogEntryTagAggregate`/`roller_weblogentrytagagg`.
