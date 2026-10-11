import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import zipfile
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox, simpledialog
from ttkthemes import ThemedTk

try:
    import tomllib
except ImportError:
    tomllib = None

system = platform.system()
is_win = system == "Windows"
is_mac = system == "Darwin"
is_linux = system == "Linux"
game_exes = ["snaptic.exe", "Snaptic.exe", "SNAPTIC.exe"]
loader_name = "winmm.dll"
rungame_url = "steam://rungameid/5273300"
here = Path(__file__).parent.resolve()
workspace = Path.home() / ".snaptic_modder"
mods_dir = workspace / "mods"
config_path = workspace / "config.json"

for d in (workspace, mods_dir):
    d.mkdir(parents=True, exist_ok=True)

def bundled(name):
    roots = [here, here / "files"]
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        roots += [Path(mei), Path(mei) / "files"]
    for base in roots:
        p = base / name
        if p.exists():
            return p
    return None

def load_config():
    if config_path.exists():
        try:
            return json.loads(config_path.read_text())
        except Exception:
            pass
    return {}

def save_config(c):
    config_path.write_text(json.dumps(c, indent=2))

def parse_toml(text):
    if tomllib:
        try:
            return tomllib.loads(text)
        except Exception:
            pass
    out = {}
    section = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            out.setdefault(section, {})
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if section:
                out[section][k.strip()] = v
            else:
                out[k.strip()] = v
    return out

def write_key(path, key):
    text = path.read_text() if path.exists() else ""
    if "encryption_key" in text:
        return
    lines = text.rstrip().splitlines()
    if "[game]" in text:
        out = []
        for line in lines:
            out.append(line)
            if line.strip() == "[game]":
                out.append('encryption_key = "' + key + '"')
    else:
        out = lines + ["", "[game]", 'encryption_key = "' + key + '"']
    path.write_text("\n".join(out) + "\n")

def steam_roots():
    out = []
    if is_win:
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "PROGRAMW6432"):
            v = os.environ.get(env)
            if v:
                out.append(Path(v) / "Steam")
        out += [Path("C:/Program Files (x86)/Steam"), Path("C:/Program Files/Steam")]
    elif is_mac:
        out.append(Path.home() / "Library/Application Support/Steam")
    else:
        out = [
            Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam",
            Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
            Path.home() / ".steam/steam",
            Path.home() / ".local/share/Steam",
            Path.home() / "snap/steam/common/.local/share/Steam",
        ]
    seen = []
    for p in out:
        try:
            rp = p.resolve()
            if p.exists() and rp not in seen:
                seen.append(rp)
        except Exception:
            pass
    return seen

def all_libraries():
    libs = []
    for root in steam_roots():
        if root not in libs:
            libs.append(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            try:
                for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(errors="ignore")):
                    p = Path(m.group(1).replace("\\", "/"))
                    if p.exists() and p not in libs:
                        libs.append(p)
            except Exception:
                pass
    return libs

def find_game():
    for lib in all_libraries():
        common = lib / "steamapps" / "common"
        if not common.exists():
            continue
        for entry in common.iterdir():
            if not entry.is_dir():
                continue
            if "snaptic" not in entry.name.lower():
                continue
            if any((entry / e).exists() for e in game_exes):
                return entry
    return None

def launch_steam(url):
    if is_win:
        cmd = ["cmd", "/c", "start", "", url]
    elif is_mac:
        cmd = ["open", url]
    else:
        cmd = None
    for candidate in (
        ["flatpak", "run", "com.valvesoftware.Steam", url],
        ["steam", url],
        ["xdg-open", url],
    ):
        if shutil.which(candidate[0]):
            cmd = candidate
            break
    if not cmd:
        return False
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def install_loader(game, log):
    src = bundled(loader_name) or bundled("gdpatch_loader.dll")
    if not src:
        log("loader not bundled")
        return False
    dst = game / loader_name
    backup = game / (loader_name + ".orig")
    try:
        if dst.exists() and not backup.exists():
            shutil.copy2(dst, backup)
        shutil.copy2(src, dst)
        log("loader installed")
        return True
    except Exception as e:
        log("loader install failed: " + str(e))
        return False

def sync_mods(game, key, log):
    target = game / "GDPatch" / "mods"
    target.mkdir(parents=True, exist_ok=True)
    n = 0
    for entry in mods_dir.iterdir():
        if not entry.is_dir():
            continue
        if not (entry / "gdpatch_mod.toml").exists():
            continue
        dst = target / entry.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(entry, dst)
        write_key(dst / "gdpatch_mod.toml", key)
        n += 1
    log("synced " + str(n) + " mod(s)")
    return n

class modder:
    def __init__(self):
        self.root = ThemedTk(theme="arc")
        self.root.title("snaptic modder")
        self.root.geometry("920x640")
        self.root.minsize(760, 500)
        
        self.root.withdraw()
        self.key = simpledialog.askstring("snaptic modder", "enter encryption key:", show="*")
        if not self.key:
            sys.exit(0)
        self.root.deiconify()

        self.cfg = load_config()
        self.game = None
        gp = self.cfg.get("game_path")
        if gp and Path(gp).exists():
            self.game = Path(gp)
        if not self.game:
            self.game = find_game()

        self.build()
        self.log("ready - " + system)
        self.log("game: " + str(self.game) if self.game else "game not found")

    def build(self):
        menubar = tk.Menu(self.root)
        fm = tk.Menu(menubar, tearoff=0)
        fm.add_command(label="set game folder", command=self.browse)
        fm.add_command(label="auto-detect", command=self.detect)
        fm.add_separator()
        fm.add_command(label="quit", command=self.root.quit)
        menubar.add_cascade(label="file", menu=fm)

        mm = tk.Menu(menubar, tearoff=0)
        mm.add_command(label="open mods folder", command=self.open_mods)
        mm.add_command(label="install from zip", command=self.install_zip)
        mm.add_command(label="refresh", command=self.refresh_mods)
        menubar.add_cascade(label="mods", menu=mm)

        am = tk.Menu(menubar, tearoff=0)
        am.add_command(label="about", command=self.about)
        menubar.add_cascade(label="help", menu=am)
        self.root.config(menu=menubar)

        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)

        sidebar = ttk.Frame(outer, width=180)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        ttk.Label(sidebar, text="snaptic", font=("", 12, "bold")).pack(
            anchor="w", padx=14, pady=(18, 0))
        ttk.Label(sidebar, text="modder", font=("", 9)).pack(
            anchor="w", padx=14, pady=(0, 18))
        for label in ("installed", "settings", "log"):
            ttk.Button(sidebar, text=label,
                       command=lambda n=label: self.nav(n)).pack(
                fill="x", padx=10, pady=2)
        ttk.Separator(sidebar, orient="horizontal").pack(fill="x", padx=10, pady=14)

        self.side_game = ttk.Label(sidebar, text="(no game)",
                                   wraplength=160, foreground="#888")
        self.side_game.pack(anchor="w", padx=14)
        self.side_status = ttk.Label(sidebar, text="", wraplength=160, foreground="#888")
        self.side_status.pack(anchor="w", padx=14, pady=(4, 0))
        self.update_side()
        ttk.Button(sidebar, text="change...", command=self.browse).pack(
            fill="x", padx=10, pady=(6, 0))

        self.stack = ttk.Frame(outer)
        self.stack.pack(side="left", fill="both", expand=True)
        self.stack.rowconfigure(0, weight=1)
        self.stack.columnconfigure(0, weight=1)

        bar = ttk.Frame(self.root)
        bar.pack(side="bottom", fill="x")
        self.status = ttk.Label(bar, text="ready", anchor="w")
        self.status.pack(side="left", padx=8, pady=3)
        self.launch_btn = ttk.Button(bar, text="launch modded",
                                      command=self.launch_clicked)
        self.launch_btn.pack(side="right", padx=8, pady=3)

        self.pages = {
            "installed": self.page_installed(),
            "settings": self.page_settings(),
            "log": self.page_log(),
        }
        for p in self.pages.values():
            p.grid(row=0, column=0, sticky="nsew")
        self.nav("installed")

    def page_installed(self):
        f = ttk.Frame(self.stack)
        top = ttk.Frame(f)
        top.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Label(top, text="installed mods", font=("", 11, "bold")).pack(side="left")
        ttk.Button(top, text="install zip",
                   command=self.install_zip).pack(side="right", padx=(4, 0))
        ttk.Button(top, text="open folder",
                   command=self.open_mods).pack(side="right", padx=(4, 0))
        ttk.Button(top, text="refresh",
                   command=self.refresh_mods).pack(side="right")

        cols = ("name", "version", "author", "id")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, w in (("name", 280), ("version", 90), ("author", 140), ("id", 180)):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="w")
        ysb = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=(0, 12))
        ysb.pack(side="left", fill="y", pady=(0, 12), padx=(0, 12))
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        return f

    def page_settings(self):
        f = ttk.Frame(self.stack)
        box = ttk.LabelFrame(f, text="game folder")
        box.pack(fill="x", padx=14, pady=14)
        self.path_var = ttk.Label(box,
                                  text=str(self.game) if self.game else "(not set)",
                                  wraplength=680)
        self.path_var.pack(anchor="w", padx=10, pady=(8, 4))
        row = ttk.Frame(box)
        row.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(row, text="detect", command=self.detect).pack(side="left")
        ttk.Button(row, text="browse", command=self.browse).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="open folder",
                   command=self.open_game_folder).pack(side="left", padx=(6, 0))

        box2 = ttk.LabelFrame(f, text="mods")
        box2.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Label(box2, text=str(mods_dir),
                  wraplength=680).pack(anchor="w", padx=10, pady=(8, 4))
        ttk.Button(box2, text="open folder",
                   command=self.open_mods).pack(anchor="w", padx=10, pady=(0, 10))
        return f

    def page_log(self):
        f = ttk.Frame(self.stack)
        top = ttk.Frame(f)
        top.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Label(top, text="log", font=("", 11, "bold")).pack(side="left")
        ttk.Button(top, text="clear", command=self.clear_log).pack(side="right")
        inner = ttk.Frame(f)
        inner.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.log_box = tk.Text(inner, wrap="word", height=10, relief="solid",
                               borderwidth=1, font=("monospace", 9))
        self.log_box.pack(side="left", fill="both", expand=True)
        ysb = ttk.Scrollbar(inner, orient="vertical", command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=ysb.set, state="disabled")
        ysb.pack(side="left", fill="y")
        return f

    def nav(self, name):
        self.pages[name].tkraise()

    def update_side(self):
        if self.game:
            self.side_game.config(text=str(self.game), foreground="")
            self.side_status.config(text="found", foreground="#2a8a2a")
        else:
            self.side_game.config(text="(no game set)", foreground="#888")
            self.side_status.config(text="")

    def log(self, msg):
        self.status.config(text=msg)
        if hasattr(self, "log_box"):
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.root.update_idletasks()

    def clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def refresh_mods(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        n = 0
        for entry in sorted(mods_dir.iterdir()):
            if not entry.is_dir():
                continue
            tp = entry / "gdpatch_mod.toml"
            if not tp.exists():
                continue
            try:
                d = parse_toml(tp.read_text())
                meta = d.get("meta", {})
                vals = (
                    meta.get("name", entry.name),
                    meta.get("version", "?"),
                    meta.get("author", "?"),
                    d.get("id", entry.name),
                )
            except Exception:
                vals = (entry.name, "?", "?", "(broken)")
            self.tree.insert("", "end", iid=entry.name, values=vals)
            n += 1
        self.log(str(n) + " mod(s)")

    def remove_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        folder = sel[0]
        target = mods_dir / folder
        if not target.exists():
            return
        if not messagebox.askyesno("remove mod", "delete " + folder + "?"):
            return
        shutil.rmtree(target)
        self.refresh_mods()

    def install_zip(self):
        p = filedialog.askopenfilename(filetypes=[("mod zip", "*.zip")])
        if not p:
            return
        try:
            with zipfile.ZipFile(p) as z:
                manifest = None
                for name in z.namelist():
                    if name.endswith("gdpatch_mod.toml"):
                        manifest = name
                        break
                if not manifest:
                    messagebox.showerror("bad zip", "no gdpatch_mod.toml inside")
                    return
                prefix = manifest.rsplit("/", 1)[0] + "/" if "/" in manifest else ""
                for name in z.namelist():
                    if prefix and not name.startswith(prefix):
                        continue
                    rel = name[len(prefix):] if prefix else name
                    if not rel:
                        continue
                    t = mods_dir / rel
                    if name.endswith("/"):
                        t.mkdir(parents=True, exist_ok=True)
                        continue
                    t.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(name) as src, open(t, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            self.refresh_mods()
            self.log("installed " + Path(p).name)
        except Exception as e:
            messagebox.showerror("install failed", str(e))

    def open_mods(self):
        self._open(mods_dir)

    def open_game_folder(self):
        if self.game:
            self._open(self.game)

    def _open(self, p):
        try:
            if is_win:
                os.startfile(str(p))
            elif is_mac:
                subprocess.run(["open", str(p)])
            else:
                subprocess.run(["xdg-open", str(p)])
        except Exception as e:
            self.log("open failed: " + str(e))

    def browse(self):
        c = filedialog.askdirectory(title="select snaptic folder")
        if not c:
            return
        self.game = Path(c)
        self.cfg["game_path"] = c
        save_config(self.cfg)
        self.path_var.config(text=c)
        self.update_side()
        self.log("game: " + c)

    def detect(self):
        g = find_game()
        if not g:
            messagebox.showinfo("not found", "no snaptic folder in steam libraries")
            return
        self.game = g
        self.cfg["game_path"] = str(g)
        save_config(self.cfg)
        self.path_var.config(text=str(g))
        self.update_side()
        self.log("game: " + str(g))

    def about(self):
        messagebox.showinfo("about",
                            "snaptic modder\n\n"
                            "mod launcher for snaptic\n"
                            "running on: " + system)

    def launch_clicked(self):
        if not self.game or not self.game.exists():
            messagebox.showerror("no game", "set the game folder first")
            return
        self.launch_btn.configure(state="disabled")
        self.nav("log")
        threading.Thread(target=self.do_launch, daemon=True).start()

    def do_launch(self):
        try:
            if not install_loader(self.game, self.log):
                return
            sync_mods(self.game, self.key, self.log)
            self.log("launching via steam")
            if launch_steam(rungame_url):
                self.log("steam launch sent")
            else:
                self.log("could not launch steam")
        finally:
            self.root.after(0, lambda: self.launch_btn.configure(state="normal"))

    def run(self):
        self.refresh_mods()
        self.root.mainloop()

def main():
    modder().run()

if __name__ == "__main__":
    main()
