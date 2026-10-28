# UbuClean

Pembersih sistem, pemantau, dan perawatan untuk **Ubuntu Desktop 24.04** (GNOME, Wayland/X11). Antarmuka GTK4 + libadwaita 1.5, teks sepenuhnya Bahasa Indonesia.

UbuClean dirancang dengan alur **pindai → pratinjau → konfirmasi → eksekusi**. GUI tidak pernah berjalan sebagai root; operasi sistem dipisahkan ke helper CLI yang dipanggil lewat `pkexec` dan hanya menerima aksi dari whitelist.

---

## Fitur

1. **Pembersih sampah** — cache apt, paket tak terpakai, kernel lama, revisi snap, runtime Flatpak, journald, crash report, cache pengguna, cache browser (opsional), file lama `/tmp`, dan Tong Sampah. Setiap kategori menampilkan estimasi ukuran dan daftar item yang bisa diperiksa.
2. **Clear RAM** — statistik RAM/swap/cache sebelum dan sesudah, `sync` + `drop_caches` level 1/2/3, serta segarkan swap yang hanya berjalan bila RAM bebas cukup.
3. **Monitor sistem** — CPU per-core, RAM, swap, disk, jaringan, suhu, grafik 60 detik, dan daftar proses teratas dengan konfirmasi sebelum mengakhiri proses. Proses sistem kritis selalu ditolak.
4. **Analisis disk** — pemindaian folder di thread terpisah dengan progress dan tombol batal, treemap, daftar folder/file terbesar, dan pencari duplikat tiga tahap (ukuran → hash parsial → hash penuh). File duplikat hanya dipindahkan ke Trash lewat Gio dan tidak pernah dihapus otomatis.
5. **Kelola startup** — autostart XDG dari `~/.config/autostart` dan `/etc/xdg/autostart`, plus user service systemd. Entri sistem dinonaktifkan lewat override pengguna tanpa menghapus berkas asli.
6. **Jadwal otomatis** — pembuatan/penghapusan `ubuclean-clean.timer` systemd user, pilihan kategori, dan mode headless `ubuclean --run-scheduled`.
7. **Beranda & Smart Clean** — ring Skor Kesehatan Sistem 0–100, kartu Disk/RAM/CPU/Sampah, rekomendasi relevan, dan pemindaian cepat.
8. **Karantina (undo)** — item dari kategori aman dipindahkan ke `~/.local/share/ubuclean/quarantine` selama 7 hari (dapat diatur) dan dapat dipulihkan dari halaman Riwayat. Kategori yang tidak masuk akal dikarantina (cache apt, journald, snap) dijelaskan di UI.
9. **Pembersih developer** — Docker per jenis objek (image dangling, container berhenti, volume, build cache), cache pip/npm/yarn/pnpm/cargo/gradle/Go, serta pencarian `node_modules`, `__pycache__`, `target/`, dan `.venv` pada proyek yang lama tidak disentuh.
10. **Uninstaller aplikasi** — daftar apt/snap/flatpak dengan ukuran, versi, dan tanggal pemasangan, pencarian dan filter sumber, proteksi paket inti, peringatan dependen, serta tawaran pembersihan sisa konfigurasi dengan pratinjau.
11. **Notifikasi pintar** — user service ringan yang memantau disk dan RAM, memberi notifikasi dengan aksi “Bersihkan sekarang” / “Buka monitor”, lengkap dengan cooldown, ambang, dan mode bisu.

---

## Kebutuhan sistem

- Ubuntu Desktop 24.04 (Python 3.12, GTK 4.14, libadwaita 1.5)
- Paket apt:

```bash
sudo apt install python3-gi python3-gi-cairo python3-psutil \
  gir1.2-gtk-4.0 gir1.2-adw-1 libnotify-bin psmisc policykit-1
```

`python3-gi-cairo` wajib untuk ring skor dan grafik Cairo. Jika tidak ada, aplikasi tetap berjalan tetapi grafik diganti tooltip peringatan.

`psutil` dibutuhkan untuk UI dan monitor; `install.sh` memasangnya. `pytest` hanya untuk pengembangan dan tidak dipasang oleh installer (gunakan venv di bawah atau `sudo apt install python3-pytest`).

## Instalasi

### Script instal

```bash
git clone <repo> ubuclean
cd ubuclean
sudo ./install.sh            # pasang dependensi + aplikasi
sudo ./install.sh --dry-run  # lihat langkah tanpa mengubah sistem
sudo ./install.sh --no-deps  # lewati apt, jika dependensi sudah ada
```

Setelah selesai, jalankan **UbuClean** dari menu aplikasi atau:

```bash
ubuclean
```

### Paket .deb

```bash
./packaging/build-deb.sh
sudo apt install ./dist/ubuclean_1.0.0_all.deb
```

### Uninstall

```bash
sudo ./uninstall.sh         # pertahankan konfigurasi, log, dan karantina
sudo ./uninstall.sh --purge # hapus juga data pengguna
```

## Menjalankan dari source (pengembangan)

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install psutil pytest
.venv/bin/python -m ubuclean
```

Atau gunakan launcher pengembangan:

```bash
bin/ubuclean
```

Helper root tidak dijalankan langsung saat pengembangan; pemanggilan `pkexec` memakai helper terpasang. Untuk menguji helper tanpa pkexec (hanya root):

```bash
sudo UBUCLEAN_HELPER_ALLOW_ROOT=1 python3 -m ubuclean.helper clean-tmp 7
```

## CLI

```bash
ubuclean                          # buka GUI
ubuclean --version
ubuclean --page clean             # buka GUI langsung di halaman tertentu

ubuclean scan --all --json        # pindai semua kategori, keluarkan JSON
ubuclean scan --category user-cache
ubuclean clean --category user-cache --dry-run   # pratinjau tanpa perubahan
ubuclean clean --category user-cache --yes       # eksekusi non-interaktif
ubuclean status                   # ringkasan RAM/swap/disk/CPU
ubuclean history --json

ubuclean --run-scheduled          # dipakai systemd timer (headless)
ubuclean --watch                  # pemantau notifikasi
```

Macam kategori: `apt-cache`, `apt-autoremove`, `kernels`, `snap-revisions`, `flatpak-unused`, `journal`, `crash`, `tmp`, `user-cache`, `browser-cache`, `trash`, `dev-docker`, `dev-caches`, `dev-projects`.

Jadwal headless **tidak** menjalankan operasi yang membutuhkan autentikasi root. Kategori seperti cache apt, journald, snap, dan `/tmp` yang butuh root akan dicatat sebagai “dilewati” di log dan ringkasan notifikasi. Kategori berisiko (kernel lama, cache browser) tidak pernah dijadwalkan.

## Keamanan

- **GUI menolak root**; `ubuclean` mengembalikan kode error bila dijalankan dengan `euid == 0`.
- **Whitelist path** di `ubuclean/core/paths.py`: hanya `~/.cache/**`, `~/.local/share/Trash/**`, karantina UbuClean, `/tmp/**`, dan `/var/crash/**`. Direktori home, `/`, `/etc`, `/boot`, `/usr`, `/var`, data pribadi (Documents/Pictures/…), `.ssh`, `.gnupg`, dan `.config` ditolak.
- Symlink di-resolve sebelum validasi dan tidak pernah diikuti saat penghapusan.
- **Helper** hanya menerima subcommand whitelist dengan validasi regex/rentang; tidak ada perintah bebas dan tidak ada `shell=True` di seluruh proyek.
- **Polkit** action `com.ubuclean.helper.run` (`auth_admin_keep`) dengan pesan Bahasa Indonesia dan path helper yang di-annotate.
- **Preview token**: eksekusi hanya diterima untuk preview yang dipindai pada sesi yang sama, sehingga penghapusan tanpa pratinjau tidak mungkin.
- **Kernel**: kernel yang sedang berjalan dan minimal satu kernel cadangan selalu dipertahankan; helper memverifikasi ulang sebelum purge.
- **Karantina**: item aman dipindahkan dulu; pemulihan mengembalikan ke path asli dan memberi nama alternatif bila bentrok.
- Setiap aksi dicatat ke `~/.local/state/ubuclean/log` (JSON Lines) dengan waktu, kategori, jumlah item, ukuran, hasil, dan error.

## Struktur proyek

```
ubuclean/
├── ubuclean/
│   ├── core/          # cleaners, ram, monitor, disk, health, startup,
│   │                  # scheduler, scheduled, notifier, uninstaller
│   ├── helper/        # helper root + validasi whitelist
│   ├── ui/            # halaman Adw, widget Cairo, style.css
│   ├── data/          # .desktop, ikon, polkit policy, unit notifier
│   ├── config.py
│   └── cli.py
├── bin/               # launcher pengembangan
├── tests/             # pytest, filesystem tiruan, FakeRunner
├── packaging/         # build-deb.sh
├── install.sh
├── uninstall.sh
└── README.md
```

## Tes

```bash
.venv/bin/pytest -q
```

Tes tidak menyentuh sistem asli: filesystem memakai `tmp_path`, subprocess memakai `FakeRunner`, dan home diisolasi. Cakupan mencakup whitelist path, symlink escape, karantina/restore, pipeline preview/clean, cleaner user & sistem, guard kernel, RAM/swap, monitor, analisis disk & duplikat, startup, scheduler, headless, notifier, uninstaller, dan CLI.

## Catatan desain & batasan

- Karantina berada di disk yang sama, jadi ruang baru benar-benar bebas setelah retensi habis (7 hari default). UI membedakan “dipindahkan ke karantina” dan “dibebaskan”.
- `drop_caches` hanya membuang cache yang akan terbentuk lagi; UI menampilkan catatan jujur dan tidak menjanjikan percepatan.
- Pemindaian disk dan duplikat berjalan di thread pekerja dan dapat dibatalkan. Preview pembersihan tidak dapat dibatalkan di tengah jalan, tetapi item tetap bisa dipulihkan dari karantina.
- Sensor suhu bergantung pada dukungan kernel/psutil; bila tidak tersedia, UI menampilkan “Sensor tidak tersedia”.
- Operasi Docker memerlukan akses ke socket Docker (grup `docker`) atau autentikasi root lewat helper. `docker system prune -a` tidak pernah dipakai.
- Notifikasi membutuhkan `libnotify-bin` (`notify-send`). Aksi notifikasi membuka halaman UbuClean yang sesuai.
- Animasi mengikuti pengaturan “kurangi animasi”/`gtk-enable-animations`; mode gelap/terang mengikuti tema sistem.
- `Adw.ShortcutsDialog` belum tersedia di libadwaita 1.5 (versi Ubuntu 24.04), sehingga F1 membuka dialog `Adw.Dialog` dengan isi pintasan yang setara.
- Uninstaller dibuka dari tombol di header halaman Bersihkan atau dari Pengaturan → Aplikasi terpasang.

## Lisensi

GPL-3.0-or-later. Lihat `LICENSE`.
