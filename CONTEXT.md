# Hypernote

Hypernote coordinates notebook execution through a Hypernote-enabled JupyterLab
server so agents and humans can work against one notebook truth.

## Language

**Shared Document**:
The server-side notebook document that all Hypernote operations use as the live notebook truth.
_Avoid_: file-only notebook truth

**Notebook File**:
The `.ipynb` artifact that persists notebook contents and outputs after the Shared Document is saved.
_Avoid_: Hypernote database, collaboration store, job history

**Collaboration Journal**:
The Jupyter real-time collaboration update store used as recoverability cache for live Shared Document state.
_Avoid_: notebook primary storage, Hypernote job history, attribution store

**Hypernote JupyterLab Server**:
A JupyterLab server launched or verified with Hypernote's required server and collaboration extensions.
_Avoid_: plain Jupyter server, separate agent server

**Open Lab Tab**:
A browser tab viewing a notebook through the Hypernote JupyterLab Server.
_Avoid_: separate Lab server, plain Lab tab

**Agent Automation**:
CLI or SDK notebook work performed through the Hypernote JupyterLab Server, whether an Open Lab Tab currently exists.
_Avoid_: separate runtime mode

## Relationships

- A **Hypernote JupyterLab Server** owns the **Shared Document**.
- A **Shared Document** saves durable notebook contents and outputs into one **Notebook File**.
- A **Collaboration Journal** is temporary server-local state; Hypernote does not make it a durable project artifact or product choice.
- An **Open Lab Tab** and **Agent Automation** must attach to the same **Hypernote JupyterLab Server**.
- **Agent Automation** does not require an **Open Lab Tab**, but it still requires the **Hypernote JupyterLab Server**.

## Example Dialogue

> **Dev:** "Can agents run notebooks without JupyterLab?"
> **Domain expert:** "No separate mode: agents use the **Hypernote JupyterLab Server** even if nobody has an **Open Lab Tab**."
>
> **Dev:** "Is the collaboration database part of Hypernote's durable state?"
> **Domain expert:** "No — durability means the **Shared Document** has saved back to the **Notebook File**."

## Flagged Ambiguities

- Agent work without an **Open Lab Tab** was previously described as a separate product mode; resolved: Hypernote only distinguishes whether an **Open Lab Tab** exists.
- "Jupyter server" was used broadly; resolved: the supported product server is a **Hypernote JupyterLab Server**.
- Jupyter's real-time collaboration store was treated as possible Hypernote state; resolved: it is a **Collaboration Journal**, not primary notebook storage or Hypernote job history.
- Persistent collaboration-store configuration was considered as a user-facing choice; resolved: Hypernote treats the **Collaboration Journal** as temporary server-local state only.
