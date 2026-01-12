#!/usr/bin/env python3
"""
Price Updater – High Performance + Live Progress + Calendar + Preview
"""

import os
import re
import threading
from datetime import datetime
import pandas as pd
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkcalendar import DateEntry

# ==========================================================
# CONSTANTS
# ==========================================================
_PREVIEW_LIMIT = 100
_KEY_RE = re.compile(r"[^A-Z0-9]+")
_DATE_RE = re.compile(r"^[A-Za-z]+ \d{1,2} \d{4}")

UPDATED_LOG = "updated_rows.csv"
NOT_UPDATED_LOG = "not_updated_rows.csv"

# ==========================================================
# UTILITIES
# ==========================================================
def clean_key(v):
    return _KEY_RE.sub("", str(v or "").upper().strip())


def read_tabular(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    return pd.read_excel(path, dtype=str)


def format_date(d):
    return d.strftime("%B %d %Y").replace(" 0", " ")


# ==========================================================
# FILE-1 PROMO MAP (ROBUST)
# ==========================================================
def build_promo_map(path, log):
    df = read_tabular(path)
    df.columns = df.columns.str.strip()

    key_col = next(
        (c for c in df.columns if "stripped" in c.lower() and "stock" in c.lower()),
        None
    ) or next(
        (c for c in df.columns if "stock" in c.lower() or "sku" in c.lower()),
        None
    )

    promo_col = next(
        (c for c in df.columns if "promo" in c.lower() and "dealer" in c.lower()),
        None
    ) or next(
        (c for c in df.columns if "promo" in c.lower()),
        None
    )

    if not key_col or not promo_col:
        raise ValueError("Required columns not found in File-1")

    df["_KEY_"] = df[key_col].map(clean_key)
    df["_PROMO_"] = pd.to_numeric(df[promo_col], errors="coerce")

    promo_map = dict(
        zip(df["_KEY_"], df["_PROMO_"])
    )

    log(f"File-1 loaded | Rows: {len(df):,} | Mapped: {len(promo_map):,}")
    return promo_map


# ==========================================================
# CORE PROCESSING (OLD LOGIC PRESERVED)
# ==========================================================
def process(df, sku_col, promo_map, new_date, ui_cb):
    df = df.copy()
    df.columns = df.columns.str.strip()

    total = len(df)

    for c in ["sitecost", "siteprice", "productnotes", "skipproductnotes"]:
        if c not in df.columns:
            df[c] = ""

    ui_cb(25, "Mapping SKUs...")
    df["_KEY_"] = df[sku_col].map(clean_key)
    df["_PROMO_"] = df["_KEY_"].map(promo_map)

    ui_cb(40, "Validating GP...")
    df["GP_NUM"] = pd.to_numeric(df.get("GP"), errors="coerce")
    df["DIV"] = 1 - df["GP_NUM"]

    valid = (
        df["_PROMO_"].notna() &
        df["GP_NUM"].notna() &
        (df["GP_NUM"] > 0) &
        (df["GP_NUM"] < 1) &
        (df["DIV"] != 0)
    )

    updated_count = int(valid.sum())
    skipped_count = total - updated_count

    ui_cb(60, f"Updating {updated_count:,} rows...")

    # Backup product notes
    df.loc[valid, "skipproductnotes"] = df.loc[valid, "productnotes"]

    # Prices (NO ROUNDING – OLD BEHAVIOR)
    df.loc[valid, "sitecost"] = df.loc[valid, "_PROMO_"]
    df.loc[valid, "siteprice"] = (
        df.loc[valid, "_PROMO_"] / df.loc[valid, "DIV"]
    )

    # Product notes – date replace only
    def update_notes(n):
        if not n:
            return new_date
        if _DATE_RE.match(n):
            return _DATE_RE.sub(new_date, n)
        return f"{new_date} {n}"

    df.loc[valid, "productnotes"] = df.loc[valid, "productnotes"].apply(update_notes)

    updated_df = df[valid].copy()
    not_updated_df = df[~valid].copy()
    not_updated_df["reason"] = "Invalid GP / SKU not found"

    preview = pd.concat(
        [updated_df.head(_PREVIEW_LIMIT), not_updated_df.head(_PREVIEW_LIMIT)]
    )

    ui_cb(90, "Finalizing...")
    return df, updated_df, not_updated_df, preview, updated_count, skipped_count


# ==========================================================
# GUI APPLICATION
# ==========================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Price Updater – Production Tool")
        self.geometry("1150x780")

        self.f1 = tk.StringVar()
        self.f2 = tk.StringVar()
        self.out = tk.StringVar()

        self._build_ui()

    # ---------------- UI ----------------
    def _build_ui(self):
        pad = dict(padx=8, pady=6)

        def row(label, var, cmd):
            r = ttk.Frame(self); r.pack(fill=tk.X, **pad)
            ttk.Label(r, text=label, width=24).pack(side=tk.LEFT)
            ttk.Entry(r, textvariable=var).pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Button(r, text="Browse", command=cmd).pack(side=tk.LEFT)

        row("File-1 (Supplier)", self.f1, lambda: self.pick(self.f1))
        row("File-2 (Site Export)", self.f2, lambda: self.pick(self.f2))
        row("Output File", self.out, lambda: self.save())

        r = ttk.Frame(self); r.pack(fill=tk.X, **pad)
        ttk.Label(r, text="Product Notes Date", width=24).pack(side=tk.LEFT)
        self.date = DateEntry(r, width=18)
        self.date.pack(side=tk.LEFT)

        ttk.Button(self, text="Run Update", command=self.run).pack(pady=8)

        # Progress
        r = ttk.Frame(self); r.pack(fill=tk.X, padx=8)
        self.progress_lbl = ttk.Label(r, text="Idle")
        self.progress_lbl.pack(side=tk.LEFT)
        self.pb = ttk.Progressbar(r, mode="determinate")
        self.pb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)

        # Preview
        self.tree = ttk.Treeview(
            self,
            columns=("status", "sku", "old_notes", "new_notes"),
            show="headings"
        )
        for c in self.tree["columns"]:
            self.tree.heading(c, text=c)
        self.tree.tag_configure("ok", background="#d4f4dd")
        self.tree.tag_configure("bad", background="#f4cccc")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    # ---------------- Helpers ----------------
    def pick(self, var):
        p = filedialog.askopenfilename()
        if p:
            var.set(p)

    def save(self):
        p = filedialog.asksaveasfilename(defaultextension=".xlsx")
        if p:
            self.out.set(p)

    def _ui_progress(self, pct, txt):
        self.after(0, lambda: (
            self.pb.config(value=pct),
            self.progress_lbl.config(text=txt)
        ))

    # ---------------- Run ----------------
    def run(self):
        if not all([self.f1.get(), self.f2.get(), self.out.get()]):
            messagebox.showerror("Missing", "All inputs required")
            return

        self.tree.delete(*self.tree.get_children())
        self.pb["value"] = 0
        self.progress_lbl.config(text="Starting...")

        def worker():
            try:
                self._ui_progress(5, "Loading File-1...")
                promo = build_promo_map(self.f1.get(), print)

                self._ui_progress(15, "Loading File-2...")
                df = read_tabular(self.f2.get())
                sku_col = next(c for c in df.columns if "sku" in c.lower())

                date_str = format_date(self.date.get_date())

                final, upd, notupd, preview, ucnt, scnt = process(
                    df, sku_col, promo, date_str, self._ui_progress
                )
                for _df in (final, upd, notupd):
                    _df.drop(columns=["_KEY_", "_PROMO_", "GP_NUM"], inplace=True, errors="ignore")

                self._ui_progress(95, "Saving files...")
                ext = os.path.splitext(self.out.get())[1]
                if ext == ".csv":
                    final.to_csv(self.out.get(), index=False)
                else:
                    final.to_excel(self.out.get(), index=False)

                folder = os.path.dirname(self.out.get())
                upd.to_csv(os.path.join(folder, UPDATED_LOG), index=False)
                notupd.to_csv(os.path.join(folder, NOT_UPDATED_LOG), index=False)

                for _, r in preview.iterrows():
                    tag = "ok" if r.name in upd.index else "bad"
                    self.tree.insert(
                        "",
                        "end",
                        values=(
                            tag,
                            r.get(sku_col),
                            r.get("skipproductnotes"),
                            r.get("productnotes")
                        ),
                        tags=(tag,)
                    )

                self._ui_progress(100, "Completed")
                messagebox.showinfo(
                    "Done",
                    f"Updated: {ucnt:,}\nSkipped: {scnt:,}"
                )

            except Exception as e:
                self._ui_progress(0, "Failed")
                messagebox.showerror("Error", str(e))

        threading.Thread(target=worker, daemon=True).start()


# ==========================================================
if __name__ == "__main__":
    App().mainloop()