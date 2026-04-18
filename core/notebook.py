import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Maximum characters for cell output before truncation
_MAX_OUTPUT_LEN = 3000
# Timeout for a single cell execution (seconds)
_CELL_TIMEOUT = 120
# Timeout for waiting on a single IOPub message (seconds)
_IOPUB_TIMEOUT = 10


class NotebookManager:
    """Manages Jupyter notebook execution with persistent kernel."""

    def __init__(self):
        self._kernel_manager = None  # jupyter_client.KernelManager
        self._kernel_client = None   # jupyter_client.KernelClient
        self._notebook = None        # parsed notebook dict
        self._notebook_path = None   # Path to .ipynb file
        self._cell_outputs = {}      # cell_index -> output string
        self._current_env = None     # Python executable path

    # ------------------------------------------------------------------
    # Notebook I/O
    # ------------------------------------------------------------------

    async def open_notebook(self, path: str) -> str:
        """Load a .ipynb file. Returns summary (cell count, cell types)."""
        nb_path = Path(path)
        if not nb_path.exists():
            return f"File not found: {path}"
        if nb_path.suffix.lower() != ".ipynb":
            return f"Not a notebook file: {path}"

        try:
            raw = await asyncio.to_thread(nb_path.read_text, encoding="utf-8")
            notebook = json.loads(raw)
        except json.JSONDecodeError as exc:
            return f"Invalid notebook JSON: {exc}"
        except OSError as exc:
            return f"Cannot read file: {exc}"

        cells = notebook.get("cells", [])
        if not cells:
            return f"Notebook has no cells: {path}"

        self._notebook = notebook
        self._notebook_path = nb_path
        self._cell_outputs = {}

        # Summarise cell types
        type_counts = {}
        for cell in cells:
            ct = cell.get("cell_type", "unknown")
            type_counts[ct] = type_counts.get(ct, 0) + 1

        parts = [f"{v} {k}" for k, v in type_counts.items()]
        summary = ", ".join(parts)
        return (
            f"Loaded {nb_path.name} ({len(cells)} cells: {summary})"
        )

    # ------------------------------------------------------------------
    # Kernel lifecycle
    # ------------------------------------------------------------------

    async def start_kernel(self, python_path: str | None = None) -> str:
        """Start a Jupyter kernel.

        If *python_path* is given it is used as the Python executable, e.g.
        ``/mnt/d/commonenv/Scripts/python.exe`` (Windows via WSL) or
        ``/home/user/venv/bin/python3`` (native Linux).
        """
        try:
            import jupyter_client  # noqa: F811
        except ImportError:
            return (
                "jupyter_client is not installed. "
                "Run: pip install jupyter_client ipykernel"
            )

        # Shut down any existing kernel first
        if self._kernel_manager is not None:
            await self.stop_kernel()

        try:
            km = jupyter_client.KernelManager()

            if python_path:
                # Point the kernel at a specific Python executable
                km.kernel_spec_manager = None  # reset
                km.kernel_cmd = [
                    python_path, "-m", "ipykernel_launcher",
                    "-f", "{connection_file}",
                ]
                self._current_env = python_path
            else:
                self._current_env = "default"

            # Start kernel in a thread (it's blocking)
            await asyncio.to_thread(km.start_kernel)

            kc = km.client()
            kc.start_channels()

            # Wait for kernel to be ready (blocks, so run in thread)
            try:
                await asyncio.to_thread(kc.wait_for_ready, timeout=30)
            except RuntimeError:
                await asyncio.to_thread(km.shutdown_kernel, now=True)
                return "Kernel started but never became ready (timeout 30s)."

            self._kernel_manager = km
            self._kernel_client = kc

            env_label = python_path or "system default"
            return f"Kernel started (Python: {env_label})"

        except Exception as exc:
            logger.exception("Failed to start kernel")
            return f"Failed to start kernel: {exc}"

    async def stop_kernel(self) -> str:
        """Shutdown the kernel and cleanup."""
        if self._kernel_manager is None:
            return "No kernel is running."
        try:
            if self._kernel_client is not None:
                self._kernel_client.stop_channels()
                self._kernel_client = None

            await asyncio.to_thread(
                self._kernel_manager.shutdown_kernel, now=True
            )
            self._kernel_manager = None
            self._current_env = None
            return "Kernel stopped."
        except Exception as exc:
            logger.exception("Error stopping kernel")
            self._kernel_manager = None
            self._kernel_client = None
            self._current_env = None
            return f"Kernel stopped with errors: {exc}"

    # ------------------------------------------------------------------
    # Cell execution
    # ------------------------------------------------------------------

    async def run_cell(self, cell_index: int) -> str:
        """Execute a single cell by 0-based index.

        Returns formatted output. Markdown cells return their source as-is.
        """
        if self._notebook is None:
            return "No notebook loaded. Use open_notebook() first."

        cells = self._notebook.get("cells", [])
        if cell_index < 0 or cell_index >= len(cells):
            return (
                f"Cell index out of range. "
                f"Valid range: 1-{len(cells)} (you asked for {cell_index + 1})"
            )

        cell = cells[cell_index]
        cell_type = cell.get("cell_type", "code")
        source = _join_source(cell.get("source", []))

        if cell_type != "code":
            # Non-code cells — just return their content
            preview = _truncate(source, _MAX_OUTPUT_LEN)
            self._cell_outputs[cell_index] = preview
            return f"[Cell {cell_index + 1} — {cell_type}]\n{preview}"

        if not source.strip():
            self._cell_outputs[cell_index] = "(empty cell)"
            return f"[Cell {cell_index + 1}] (empty cell)"

        if self._kernel_client is None:
            return "No kernel running. Use start_kernel() first."

        # Execute on the kernel
        try:
            msg_id = self._kernel_client.execute(source)
        except Exception as exc:
            err = f"Failed to send code to kernel: {exc}"
            self._cell_outputs[cell_index] = err
            return f"[Cell {cell_index + 1}] {err}"

        # Collect output from the IOPub channel
        output_parts = []
        success = True

        try:
            output_parts, success = await asyncio.to_thread(
                self._collect_output, msg_id
            )
        except Exception as exc:
            err = f"Error collecting output: {exc}"
            self._cell_outputs[cell_index] = err
            return f"[Cell {cell_index + 1}] {err}"

        output = "\n".join(output_parts).strip() or "(no output)"
        output = _truncate(output, _MAX_OUTPUT_LEN)
        self._cell_outputs[cell_index] = output

        status = "OK" if success else "ERROR"
        return f"[Cell {cell_index + 1} — {status}]\n{output}"

    def _collect_output(self, msg_id: str) -> tuple[list[str], bool]:
        """Synchronous helper — collects IOPub messages until execution idle.

        Returns (output_parts, success).
        """
        kc = self._kernel_client
        parts: list[str] = []
        success = True
        deadline = asyncio.get_event_loop().time() + _CELL_TIMEOUT if False else None
        # We use wall-clock tracking instead
        import time
        end_time = time.monotonic() + _CELL_TIMEOUT

        while True:
            if time.monotonic() > end_time:
                parts.append("(timed out waiting for cell to finish)")
                success = False
                break

            try:
                msg = kc.get_iopub_msg(timeout=_IOPUB_TIMEOUT)
            except Exception:
                # Timeout on a single message poll — keep waiting unless
                # we've exceeded the overall deadline
                continue

            # Only process messages belonging to our execution
            if msg.get("parent_header", {}).get("msg_id") != msg_id:
                continue

            msg_type = msg.get("msg_type", "")
            content = msg.get("content", {})

            if msg_type == "stream":
                text = content.get("text", "")
                if text:
                    parts.append(text)

            elif msg_type == "execute_result":
                data = content.get("data", {})
                text = data.get("text/plain", "")
                if text:
                    parts.append(text)

            elif msg_type == "display_data":
                data = content.get("data", {})
                # Prefer text, note images
                text = data.get("text/plain", "")
                if text:
                    parts.append(text)
                if "image/png" in data or "image/jpeg" in data:
                    parts.append("[image output]")
                html = data.get("text/html", "")
                if html and not text:
                    # Show a short note — HTML is rarely useful as text
                    parts.append("[HTML output]")

            elif msg_type == "error":
                ename = content.get("ename", "Error")
                evalue = content.get("evalue", "")
                tb = content.get("traceback", [])
                # Traceback entries may contain ANSI escape codes
                tb_clean = "\n".join(_strip_ansi(line) for line in tb)
                parts.append(f"{ename}: {evalue}\n{tb_clean}")
                success = False

            elif msg_type == "status":
                if content.get("execution_state") == "idle":
                    break

        return parts, success

    # ------------------------------------------------------------------
    # Batch execution
    # ------------------------------------------------------------------

    async def run_all(self, callback=None) -> list[tuple[int, str, bool]]:
        """Run all cells sequentially.

        Returns list of ``(index, output, success)``.
        *callback* is an optional ``async def callback(cell_index, output, success)``
        called after each cell completes.
        Stops on error and returns what completed so far.
        """
        return await self.run_from(0, callback=callback)

    async def run_from(
        self, start_index: int, callback=None
    ) -> list[tuple[int, str, bool]]:
        """Run from *start_index* to the end.

        Same return and *callback* semantics as :meth:`run_all`.
        """
        if self._notebook is None:
            return [(start_index, "No notebook loaded.", False)]

        cells = self._notebook.get("cells", [])
        results: list[tuple[int, str, bool]] = []

        for idx in range(start_index, len(cells)):
            output = await self.run_cell(idx)
            cell = cells[idx]
            cell_type = cell.get("cell_type", "code")

            # Determine success: non-code cells always succeed
            if cell_type != "code":
                ok = True
            else:
                ok = not output.startswith(f"[Cell {idx + 1} — ERROR]")

            results.append((idx, output, ok))

            if callback is not None:
                try:
                    await callback(idx, output, ok)
                except Exception:
                    logger.exception("Callback error for cell %d", idx + 1)

            if not ok:
                break

        return results

    # ------------------------------------------------------------------
    # Cell inspection / editing
    # ------------------------------------------------------------------

    def get_cell_source(self, cell_index: int) -> str:
        """Get the source code of a cell (0-based index)."""
        if self._notebook is None:
            return "No notebook loaded."
        cells = self._notebook.get("cells", [])
        if cell_index < 0 or cell_index >= len(cells):
            return f"Cell index out of range (valid: 1-{len(cells)})."
        source = _join_source(cells[cell_index].get("source", []))
        cell_type = cells[cell_index].get("cell_type", "code")
        return f"[Cell {cell_index + 1} — {cell_type}]\n{source}"

    def get_cell_output(self, cell_index: int) -> str:
        """Get the last execution output of a cell (0-based index)."""
        if cell_index in self._cell_outputs:
            return self._cell_outputs[cell_index]
        return f"Cell {cell_index + 1} has not been executed yet."

    def edit_cell(self, cell_index: int, new_source: str) -> str:
        """Replace the source of a cell and persist to disk.

        Returns a confirmation message.
        """
        if self._notebook is None:
            return "No notebook loaded."
        cells = self._notebook.get("cells", [])
        if cell_index < 0 or cell_index >= len(cells):
            return f"Cell index out of range (valid: 1-{len(cells)})."

        # Notebook format stores source as a list of lines (each ending with \n)
        # except the last line which may omit the trailing newline.
        lines = new_source.split("\n")
        source_list = [line + "\n" for line in lines[:-1]]
        if lines:
            source_list.append(lines[-1])  # last line without trailing \n

        cells[cell_index]["source"] = source_list

        # Clear cached output for this cell
        self._cell_outputs.pop(cell_index, None)

        # Persist to disk
        if self._notebook_path is not None:
            try:
                self._notebook_path.write_text(
                    json.dumps(self._notebook, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                return (
                    f"Cell {cell_index + 1} updated in memory but "
                    f"failed to write to disk: {exc}"
                )

        preview = new_source[:120]
        if len(new_source) > 120:
            preview += "..."
        return f"Cell {cell_index + 1} updated: {preview}"

    def get_cell_list(self) -> str:
        """Return a formatted list of all cells with types and first-line preview."""
        if self._notebook is None:
            return "No notebook loaded."
        cells = self._notebook.get("cells", [])
        if not cells:
            return "Notebook has no cells."

        lines = []
        for i, cell in enumerate(cells):
            cell_type = cell.get("cell_type", "unknown")
            source = _join_source(cell.get("source", []))
            first_line = source.split("\n", 1)[0].strip()
            if len(first_line) > 80:
                first_line = first_line[:77] + "..."
            if not first_line:
                first_line = "(empty)"

            status = ""
            if i in self._cell_outputs:
                # Check if the stored output indicates error
                out = self._cell_outputs[i]
                if f"[Cell {i + 1} — ERROR]" in out or "Error:" in out:
                    status = " [ERR]"
                else:
                    status = " [OK]"

            lines.append(
                f"  {i + 1}. [{cell_type}]{status} {first_line}"
            )

        header = f"{self.notebook_name} — {len(cells)} cells"
        return header + "\n" + "\n".join(lines)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether a kernel is currently active."""
        if self._kernel_manager is None:
            return False
        try:
            return self._kernel_manager.is_alive()
        except Exception:
            return False

    @property
    def notebook_name(self) -> str:
        """Current notebook filename or placeholder."""
        if self._notebook_path is not None:
            return self._notebook_path.name
        return "No notebook loaded"


# ======================================================================
# Helpers
# ======================================================================

def _join_source(source) -> str:
    """Notebook cells store source as either a string or list of strings."""
    if isinstance(source, list):
        return "".join(source)
    return source or ""


def _truncate(text: str, limit: int) -> str:
    """Truncate text to *limit* characters, appending an indicator."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (truncated)"


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from a string."""
    import re
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
