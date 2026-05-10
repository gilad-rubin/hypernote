# Hypernote

Hypernote coordinates notebook execution through a Hypernote-enabled JupyterLab
server so agents and humans can work against one notebook truth.

## Language

**Shared Document**:
The server-side notebook document that all Hypernote operations use as the live notebook truth.
_Avoid_: file-only notebook truth

**Hypernote JupyterLab Server**:
A JupyterLab server launched or verified with Hypernote's required server and collaboration extensions.
_Avoid_: plain Jupyter server, separate agent server

**Open Lab Tab**:
A browser tab viewing a notebook through the Hypernote JupyterLab Server.
_Avoid_: separate Lab server, plain Lab tab

**Agent Automation**:
CLI or SDK notebook work performed through the Hypernote JupyterLab Server, whether or not an Open Lab Tab currently exists.
_Avoid_: separate runtime mode

## Relationships

- A **Hypernote JupyterLab Server** owns the **Shared Document**.
- An **Open Lab Tab** and **Agent Automation** must attach to the same **Hypernote JupyterLab Server**.
- **Agent Automation** does not require an **Open Lab Tab**, but it still requires the **Hypernote JupyterLab Server**.

## Example Dialogue

> **Dev:** "Can agents run notebooks without JupyterLab?"
> **Domain expert:** "No separate mode: agents use the **Hypernote JupyterLab Server** even if nobody has an **Open Lab Tab**."

## Flagged Ambiguities

- Agent work without an **Open Lab Tab** was previously described as a separate product mode; resolved: Hypernote only distinguishes whether an **Open Lab Tab** exists.
- "Jupyter server" was used broadly; resolved: the supported product server is a **Hypernote JupyterLab Server**.
