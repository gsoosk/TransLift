# apache_roller-0004 — systematic problems

- **`checkPermission`'s `WeblogPermission` branch is walked even when the argument can only be a
  `GlobalPermission`.** `JPAUserManagerImpl.checkPermission(RollerPermission perm, User user)`
  (JPAUserManagerImpl.java:354-380) opens with `if (perm instanceof WeblogPermission)`, and the
  extractor's interprocedural walk includes that branch's reads (`getWeblogPermission`,
  roller_permission; the nested `getWeblogByHandle`, weblog) for every call to `checkPermission`,
  regardless of the statically-known type of the argument passed in. In this batch four call sites
  pass a `GlobalPermission` built from a `List<String>` of actions (`GlobalPermission(List<String>)`,
  GlobalPermission.java:79), which is a sibling of `WeblogPermission` (GlobalPermission extends
  `RollerPermission`, WeblogPermission extends `ObjectPermission`), so the `instanceof` check is
  always false and the branch's reads never run:
  - `1a8158849af8` UserEdit.save: `mgr.checkPermission(adminPerm, user)` (UserEdit.java:200) with
    `adminPerm = new GlobalPermission(Collections.singletonList(GlobalPermission.ADMIN))` (:196-197).
  - `26d7b7387a1a` UserEdit.execute and `c4c85532f33f` UserEdit.firstSave (which calls execute):
    `CreateUserBean.copyFrom:184-185` builds the same kind of `GlobalPermission(List<String>)` and
    calls `checkPermission`.
  - `efd345b9c659` UserDataServlet.doGet: `User.hasGlobalPermissions:226-227` builds
    `new GlobalPermission(actions)` and calls `checkPermission(perm, this)`.
  By contrast, `6e0aaa641791` CommentDataServlet.doGet reaches `checkPermission` through
  `Weblog.hasUserPermissions:373-374`, which really does construct a `WeblogPermission`, so the same
  branch is live there and its ops were kept. Likely cause: the extractor resolves `checkPermission`'s
  body once per declared signature and does not narrow `instanceof` branches by the argument's static
  type at each call site. A fix would need to track, per call site, whether the first argument's
  static type can be `WeblogPermission`, and drop the branch's effects otherwise.
