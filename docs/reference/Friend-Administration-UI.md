# Friend Onboarding System - Administration & UI



## Impersonate / View as

The admin panel provides an admin-only preview capability. Each friend row includes a **View as** link pointing to `/admin/impersonate/<email>`.

### Identity Resolution

* `session["imp_user"]` stores the impersonated identity. This key is honored only for sessions belonging to a real admin.
* `_friend_email()` returns the effective (impersonated) identity. All user-facing views render using this identity.
* `_is_google_admin()` uses the REAL session email. Admin rights and the HAI gate identity remain unaffected by impersonation. HAI identifies the operator as the admin (e.g., the owner), preventing writes to the friend's corpus.

### Safety Controls

Every side-effecting step route is enforced as a dry-run via `_preview_block()` (covering plex, seerr, discord, roundtable, and hai). The interface displays a "preview, no changes" notice and performs no mutations. A sticky banner appears on every page during impersonation. Exit is available at `/admin/impersonate/exit`.

This feature satisfies the administrative requirement to inspect test copies without modifying data.

## Admin capability editing
Admin dashboard (`/admin/friend/<email>`) provides **⚙ Manage** controls with capability checkboxes. Edits write to the record's `caps` field live; no re-invite is required.

## Admin Panel Design: Person-Centric Roster

**Design Fact:** The admin panel operates on a person-centric model keyed on email address. A single unified roster replaces the previous disjoint Codes and Friends tables.

**Data Model & Conformance:**
- **Person Key:** Every row in the roster is keyed to a unique person record identified by email.
- **Unified Actions:** Each row exposes a single action set: send onboarding/welcome link, manage caps, and view-as.
- **Invite Binding:** Invites are bound to a specific intended email address. Redemption reconciles with the existing record; it never creates duplicates.
- **Access Management:** Codes are reserved exclusively for genuinely new people with no existing record. Existing friends do not use codes for access changes or re-invitations.

**Implementation Rules:**
1. **Merge Caps on Redemption:** If an existing friend redeems a link, the system merges newly-granted caps into their existing record.
2. **Admin Exclusion:** The admin’s own record (`owner`) is excluded from the friends roster to prevent noise.
3. **Duplicate Prevention:** Since `store.ensure` is email-keyed, authentication with an address different from a backfilled record will not create a second record for the same person; the invite binding ensures correlation.

**Status:** This design is implemented. Current operational state and procedures are in the Runbook.
