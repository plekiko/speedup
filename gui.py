import os
import sys
import threading
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

def find_python() -> str:
    # Use the same interpreter that launched the GUI
    return sys.executable


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Speedup GUI")
        self.geometry("820x520")

        self.speedup_py = Path(__file__).with_name("speedup.py")
        if not self.speedup_py.exists():
            messagebox.showerror("Missing file", f"Could not find {self.speedup_py}")
            self.destroy()
            return

        self.mode = tk.StringVar(value="folder")
        self.input_path = tk.StringVar(value="")
        self.out_dir = tk.StringVar(value="")
        self.speed = tk.StringVar(value="50")
        self.fps_normal = tk.StringVar(value="30")
        self.fps_fast = tk.StringVar(value="30")
        self.crf = tk.StringVar(value="20")
        self.preset = tk.StringVar(value="medium")
        self.no_audio = tk.BooleanVar(value=True)
        self.start = tk.StringVar(value="")
        self.end = tk.StringVar(value="")

        self.proc = None
        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="Mode:").pack(side="left")
        ttk.Radiobutton(top, text="Folder (bulk)", variable=self.mode, value="folder",
                        command=self._mode_changed).pack(side="left", padx=6)
        ttk.Radiobutton(top, text="Single file", variable=self.mode, value="file",
                        command=self._mode_changed).pack(side="left")

        inp = ttk.Frame(self)
        inp.pack(fill="x", **pad)

        ttk.Label(inp, text="Input:").grid(row=0, column=0, sticky="w")
        self.input_entry = ttk.Entry(inp, textvariable=self.input_path)
        self.input_entry.grid(row=0, column=1, sticky="ew")
        self.browse_in = ttk.Button(inp, text="Browse…", command=self._browse_input)
        self.browse_in.grid(row=0, column=2, padx=6)

        ttk.Label(inp, text="Out dir (folder mode):").grid(row=1, column=0, sticky="w")
        self.out_entry = ttk.Entry(inp, textvariable=self.out_dir)
        self.out_entry.grid(row=1, column=1, sticky="ew")
        self.browse_out = ttk.Button(inp, text="Browse…", command=self._browse_outdir)
        self.browse_out.grid(row=1, column=2, padx=6)

        inp.columnconfigure(1, weight=1)

        opts = ttk.LabelFrame(self, text="Options")
        opts.pack(fill="x", **pad)

        ttk.Label(opts, text="Speed:").grid(row=0, column=0, sticky="w")
        ttk.Entry(opts, textvariable=self.speed, width=10).grid(row=0, column=1, sticky="w")

        ttk.Label(opts, text="FPS (normal):").grid(row=0, column=2, sticky="w", padx=(18, 0))
        ttk.Entry(opts, textvariable=self.fps_normal, width=10).grid(row=0, column=3, sticky="w")

        ttk.Label(opts, text="FPS (sped):").grid(row=0, column=4, sticky="w", padx=(18, 0))
        ttk.Entry(opts, textvariable=self.fps_fast, width=10).grid(row=0, column=5, sticky="w")

        ttk.Label(opts, text="CRF:").grid(row=1, column=0, sticky="w")
        ttk.Entry(opts, textvariable=self.crf, width=10).grid(row=1, column=1, sticky="w")

        ttk.Label(opts, text="Preset:").grid(row=1, column=2, sticky="w", padx=(18, 0))
        ttk.Combobox(opts, textvariable=self.preset, width=12, state="readonly",
                     values=["ultrafast","superfast","veryfast","faster","fast","medium","slow","slower","veryslow"]).grid(
            row=1, column=3, sticky="w"
        )

        ttk.Checkbutton(opts, text="No audio (global)", variable=self.no_audio).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

        ttk.Label(opts, text="Start (SS/MM:SS/HH:MM:SS):").grid(row=2, column=2, sticky="w", padx=(18, 0))
        self.start_entry = ttk.Entry(opts, textvariable=self.start, width=10)
        self.start_entry.grid(row=2, column=3, sticky="w")

        ttk.Label(opts, text="End (SS/MM:SS/HH:MM:SS):").grid(row=2, column=4, sticky="w", padx=(18, 0))
        self.end_entry = ttk.Entry(opts, textvariable=self.end, width=10)
        self.end_entry.grid(row=2, column=5, sticky="w")


        btns = ttk.Frame(self)
        btns.pack(fill="x", **pad)

        self.run_btn = ttk.Button(btns, text="Run", command=self._run)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)

        self.status = ttk.Label(btns, text="Idle")
        self.status.pack(side="right")

        logf = ttk.LabelFrame(self, text="Log")
        logf.pack(fill="both", expand=True, **pad)

        self.log = tk.Text(logf, wrap="word")
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

        self._mode_changed()

    def _mode_changed(self):
        is_folder = self.mode.get() == "folder"

        self.out_entry.configure(state=("normal" if is_folder else "disabled"))
        self.browse_out.configure(state=("normal" if is_folder else "disabled"))

        is_file = not is_folder
        self.start_entry.configure(state=("normal" if is_file else "disabled"))
        self.end_entry.configure(state=("normal" if is_file else "disabled"))

    def _browse_input(self):
        if self.mode.get() == "folder":
            p = filedialog.askdirectory(title="Select folder with MP4s")
            if p:
                self.input_path.set(p)
                # default out dir
                if not self.out_dir.get():
                    self.out_dir.set(str(Path(p) / "_processed"))
        else:
            p = filedialog.askopenfilename(
                title="Select input MP4",
                filetypes=[("MP4 files", "*.mp4"), ("All files", "*.*")]
            )
            if p:
                self.input_path.set(p)

    def _browse_outdir(self):
        p = filedialog.askdirectory(title="Select output directory")
        if p:
            self.out_dir.set(p)

    def _append_log(self, s: str):
        self.log.configure(state="normal")
        self.log.insert("end", s)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _build_cmd(self) -> list[str]:
        inp = self.input_path.get().strip()
        if not inp:
            raise ValueError("Input is required")

        cmd = [find_python(), str(self.speedup_py), inp]

        # Folder mode: speedup.py ignores "output" arg. We pass --out-dir.
        if self.mode.get() == "folder":
            outd = self.out_dir.get().strip()
            if outd:
                cmd += ["--out-dir", outd]
        else:
            # Single-file mode requires output argument, so create one next to input.
            in_path = Path(inp)
            if in_path.suffix.lower() != ".mp4":
                raise ValueError("Single-file input must be an .mp4")
            out_path = in_path.with_name(in_path.stem + "_out.mp4")
            cmd += [str(out_path)]

        cmd += ["--speed", self.speed.get().strip()]
        cmd += ["--fps-normal", self.fps_normal.get().strip()]
        cmd += ["--fps-fast", self.fps_fast.get().strip()]
        cmd += ["--crf", self.crf.get().strip()]
        cmd += ["--preset", self.preset.get().strip()]

        if self.mode.get() == "file":
            s = self.start.get().strip()
            e = self.end.get().strip()
            if s:
                cmd += ["--start", s]
            if e:
                cmd += ["--end", e]

        if self.no_audio.get():
            cmd += ["--no-audio"]

        return cmd

    def _run(self):
        if self.proc is not None:
            return

        try:
            cmd = self._build_cmd()
        except Exception as e:
            messagebox.showerror("Invalid settings", str(e))
            return

        self._append_log(f"$ {' '.join(cmd)}\n\n")
        self.status.configure(text="Running…")
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        def worker():
            try:
                self.proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                assert self.proc.stdout is not None
                for line in self.proc.stdout:
                    self.after(0, self._append_log, line)
                rc = self.proc.wait()
                self.after(0, self._append_log, f"\n[exit code {rc}]\n")
            except Exception as e:
                self.after(0, self._append_log, f"\n[error] {e}\n")
            finally:
                self.proc = None
                self.after(0, self._done)

        threading.Thread(target=worker, daemon=True).start()

    def _done(self):
        self.status.configure(text="Idle")
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def _stop(self):
        if self.proc is None:
            return
        try:
            self.proc.terminate()
            self._append_log("\n[terminated]\n")
        except Exception as e:
            self._append_log(f"\n[stop error] {e}\n")


if __name__ == "__main__":
    App().mainloop()
