Kamu adalah senior engineer Linux desktop. Buatkan aplikasi GUI utility untuk Ubuntu Desktop 24.04 (GNOME, Wayland/X11) bernama "UbuClean". Fungsinya membersihkan sampah sistem, mengosongkan cache RAM, dan menyediakan beberapa tools perawatan sistem.

## Stack
- Python 3.12, PyGObject dengan GTK4 + libadwaita (tampilan native GNOME, mengikuti tema terang/gelap sistem)
- psutil untuk data sistem. Hindari dependensi lain kecuali memang perlu.
- Semua teks antarmuka dalam Bahasa Indonesia.

## Arsitektur & hak akses
- GUI TIDAK boleh berjalan sebagai root.
- Operasi yang butuh root dipisah ke satu helper CLI kecil (`ubuclean-helper`) yang dipanggil lewat `pkexec` dengan polkit policy. Helper hanya menerima aksi dari whitelist (bukan perintah bebas), dan memvalidasi semua argumen.
- Dilarang memakai `shell=True`. Semua subprocess pakai list argumen dengan timeout.
- Pisahkan logika (core) dari UI supaya core bisa dites tanpa GUI dan dipakai juga oleh mode CLI.

## Fitur 1: Pembersih sampah
Setiap kategori punya checkbox, estimasi ukuran, dan daftar file yang bisa dilihat sebelum dihapus:
- Cache apt (`apt-get clean`) dan paket tak terpakai (`autoremove`)
- Kernel lama (JANGAN pernah menghapus kernel yang sedang berjalan maupun satu kernel cadangan)
- Revisi snap lama yang sudah disabled, dan flatpak yang tidak terpakai
- Log journald lama (`journalctl --vacuum-time`) dan crash report di `/var/crash`
- Cache pengguna di `~/.cache` (termasuk thumbnail), plus cache browser (Firefox, Chrome, Chromium) sebagai opsi terpisah yang tidak dicentang secara default
- File lama di `/tmp` (hanya yang tidak sedang dipakai dan lebih tua dari N hari)
- Tong sampah (Trash)

## Fitur 2: Clear RAM
- Tampilkan RAM, swap, dan cache/buffer saat ini, lalu hasil sebelum dan sesudah.
- Aksi: `sync` lalu tulis ke `/proc/sys/vm/drop_caches` (pilihan level 1/2/3), lewat helper.
- Opsi "Segarkan swap" (`swapoff -a && swapon -a`) HANYA boleh jalan jika RAM bebas cukup untuk menampung isi swap. Cek dulu dan batalkan dengan pesan jelas jika tidak cukup.
- Beri catatan jujur di UI: drop caches hanya membuang cache yang akan terbentuk lagi dan tidak selalu membuat sistem lebih cepat.

## Fitur 3: Monitor sistem
- Dashboard real-time (refresh tiap 1-2 detik): CPU per-core, RAM, swap, disk, jaringan, suhu (jika tersedia)
- Daftar proses teratas berdasarkan CPU/RAM, dengan tombol akhiri proses (konfirmasi dulu, dan tolak untuk proses sistem kritis)
- Grafik riwayat singkat (misalnya 60 detik terakhir), digambar dengan Gtk.DrawingArea atau Cairo

## Fitur 4: Analisis disk
- Scan folder pilihan dan tampilkan folder/file terbesar (treemap atau daftar terurut), scan di thread terpisah dengan progress bar dan tombol batal
- Pencari file duplikat: kelompokkan berdasarkan ukuran, lalu hash parsial, lalu hash penuh. Tampilkan hasilnya, pengguna memilih sendiri mana yang dihapus. JANGAN pernah menghapus otomatis.
- Penghapusan dari fitur ini dikirim ke Trash (via Gio), bukan dihapus permanen.

## Fitur 5: Kelola startup
- Daftar aplikasi autostart dari `~/.config/autostart` dan `/etc/xdg/autostart`, plus user service systemd yang aktif
- Toggle aktif/nonaktif tanpa menghapus file asli (untuk entri sistem, buat override di folder pengguna)
- Tampilkan nama, deskripsi, dan perintah yang dijalankan

## Fitur 6: Jadwal otomatis
- Atur pembersihan terjadwal (harian/mingguan/bulanan) lewat systemd user timer, dibuat dan dihapus dari GUI
- Pengguna memilih kategori yang ikut dibersihkan. Kategori berisiko (kernel lama, cache browser) tidak boleh ikut jadwal otomatis.
- Mode headless: `ubuclean --run-scheduled` tanpa membuka GUI, dengan hasil dicatat ke log dan notifikasi desktop

## Fitur 7: Beranda & Smart Clean
- Halaman pertama yang dilihat pengguna: ring/gauge besar "Skor Kesehatan Sistem" (0-100) yang dihitung dari ruang disk, penggunaan RAM/swap, jumlah sampah yang terdeteksi, dan beban startup. Tampilkan penjelasan singkat apa yang menurunkan skor.
- Tombol utama "Pindai" untuk memindai semua kategori sekaligus, lalu ringkasan hasil ("2,4 GB bisa dibebaskan") dengan tombol "Bersihkan". Tetap lewat preview dan konfirmasi.
- Kartu "Rekomendasi" yang hanya menampilkan tindakan relevan (mis. "Disk 91% penuh, ada 3,1 GB cache lama").

## Fitur 8: Karantina (undo)
- Item yang dibersihkan dari kategori yang aman dipindahkan dulu ke `~/.local/share/ubuclean/quarantine` selama 7 hari (bisa diatur), baru dihapus permanen otomatis.
- Halaman riwayat memiliki tombol "Pulihkan" per aksi. Simpan metadata path asli agar pemulihan akurat.
- Kategori yang tidak masuk akal dikarantina (cache apt, log journald, revisi snap) tetap dibersihkan langsung, dan UI menjelaskan perbedaannya.

## Fitur 9: Pembersih developer
- Docker (jika terpasang): image dangling, container berhenti, volume tak terpakai, build cache. Tampilkan per item dengan ukuran, JANGAN langsung `system prune -a`.
- Cache pip, npm, yarn, pnpm, cargo, gradle, dan Go build cache
- Pencari folder `node_modules`, `__pycache__`, `target/`, dan `.venv` pada proyek yang tidak disentuh lebih dari N hari (default 90). Pengguna memilih folder induk yang dipindai.
- Semua bersifat opsional dan tidak dicentang secara default.

## Fitur 10: Uninstaller aplikasi
- Daftar aplikasi terpasang (apt, snap, flatpak) dengan ikon, ukuran, dan tanggal pemasangan, plus pencarian dan filter sumber
- Hapus aplikasi lalu tawarkan pembersihan sisa konfigurasi/cache di `~/.config`, `~/.cache`, `~/.local/share` milik aplikasi itu (dengan preview)
- Tolak penghapusan paket inti sistem (ubuntu-desktop, gnome-shell, linux-image-*, dll.) dan beri peringatan untuk paket dengan banyak dependen

## Fitur 11: Notifikasi pintar
- Layanan latar ringan (user service) yang memantau disk dan RAM, dan mengirim notifikasi desktop saat melewati ambang yang bisa diatur (mis. disk > 90%, RAM > 90% selama 5 menit)
- Notifikasi punya tombol aksi ("Bersihkan sekarang", "Buka monitor") dan tidak spam (cooldown, bisa dibisukan)

## Desain UI (prioritas tinggi)
Tampilan harus modern, minimalis, dan enak dilihat, terasa seperti aplikasi GNOME kelas premium.

Prinsip visual:
- Gunakan libadwaita >= 1.5 (versi di Ubuntu 24.04): AdwApplicationWindow, AdwNavigationSplitView (sidebar + konten), AdwToolbarView, AdwPreferencesGroup, AdwActionRow/AdwExpanderRow, AdwSwitchRow, AdwAlertDialog, AdwToast, AdwStatusPage. Jangan membuat widget kustom kalau widget Adw sudah ada.
- Layout bergaya kartu: sudut membulat 12-16px, bayangan sangat halus, banyak ruang kosong (padding 16-24px, jarak antar kartu konsisten pada kelipatan 8px)
- Satu warna aksen dominan (ikuti aksen sistem / gunakan variabel `@accent_bg_color`). Warna semantik hijau/kuning/merah hanya untuk status (sehat/waspada/kritis), bukan dekorasi.
- Tipografi bersih: angka metrik besar dan tebal (gaya `title-1`/`large-title`), label kecil dengan kontras lebih rendah. Maksimal 2 ukuran font per kartu.
- Ikon symbolic konsisten (set Adwaita symbolic), tanpa emoji
- Mode terang dan gelap otomatis mengikuti sistem, kedua mode harus diuji agar kontrasnya baik (minimal WCAG AA)
- Kustomisasi lewat satu file `style.css` yang dimuat dengan Gtk.CssProvider, memakai named colors libadwaita supaya tema tetap konsisten

Struktur layar:
- Sidebar: Beranda, Bersihkan, Memori, Monitor, Disk, Startup, Jadwal, Riwayat, Pengaturan (ikon + label, bisa collapse di jendela sempit)
- Beranda: ring skor kesehatan di tengah atas, di bawahnya grid kartu kecil (Disk, RAM, CPU, Sampah terdeteksi) dan kartu Rekomendasi
- Bersihkan: daftar kategori sebagai baris dengan switch dan ukuran, detail file disembunyikan di expander (progressive disclosure), tombol aksi utama menempel di bawah
- Monitor: grafik garis halus dengan area gradien tipis, nilai live besar di atas tiap grafik, tanpa grid yang ramai
- Setiap halaman kosong/loading memakai AdwStatusPage atau skeleton, bukan layar kosong

Gerak & umpan balik:
- Animasi halus dan singkat (150-300 ms, easing ease-out) dengan Adw.TimedAnimation: ring skor terisi saat dibuka, angka ukuran "menghitung naik" saat hasil pindai muncul, transisi antar halaman
- Progress bar dan tombol menunjukkan keadaan (memindai, membersihkan, selesai) tanpa memblokir UI. Operasi berat selalu di thread terpisah.
- Setelah pembersihan, tampilkan layar hasil yang memuaskan: "Berhasil membebaskan 2,4 GB" dengan rincian per kategori
- Gunakan AdwToast untuk konfirmasi kecil (dengan tombol "Urungkan" bila relevan), AdwAlertDialog hanya untuk keputusan penting

## Aturan keamanan (wajib)
1. Alur default selalu: scan, lalu preview (daftar item + ukuran), lalu konfirmasi, lalu eksekusi. Tidak ada penghapusan tanpa preview.
2. Sediakan mode dry-run yang menampilkan hasil tanpa mengubah apa pun (juga lewat flag `--dry-run` di CLI).
3. Whitelist path yang boleh dihapus. Tolak path di luar whitelist, resolve symlink sebelum menghapus, dan jangan pernah menyentuh `/`, `/home` secara langsung, `/etc`, `/boot`, `/usr`, maupun data pribadi (Documents, Pictures, dll.).
4. Catat setiap aksi (waktu, kategori, item, ukuran, hasil) ke `~/.local/state/ubuclean/log`, dan sediakan halaman riwayat di GUI.
5. Tangani error dengan baik (izin ditolak, file sedang dipakai, disk read-only) tanpa membuat aplikasi crash. Satu item gagal tidak menghentikan yang lain.

## Struktur proyek & deliverables
- Struktur modular: `core/` (cleaners, ram, monitor, disk, startup, scheduler), `ui/`, `helper/`, `data/` (polkit policy, .desktop, ikon), `tests/`
- Unit test (pytest) untuk core, dengan filesystem tiruan (tmp_path) sehingga tes tidak menyentuh sistem asli
- `README.md` berisi cara instal dependensi (apt), cara menjalankan, dan penjelasan fitur
- File `.desktop` dan script instal sederhana. Bonus: paket `.deb`.
- Type hints dan docstring ringkas di semua fungsi publik

## Cara kerja
1. Sebelum menulis kode, tampilkan rencana singkat: struktur folder dan urutan pengerjaan. Tunggu persetujuanku.
2. Kerjakan bertahap, per modul. Setelah tiap tahap, jalankan tes dan jelaskan singkat apa yang sudah jadi.
3. Jika ada keputusan desain yang ambigu, tanya dulu, jangan menebak.

Responsif & aksesibilitas:
- Gunakan AdwBreakpoint agar layout menyesuaikan lebar jendela (sidebar collapse, grid 2 kolom menjadi 1)
- Semua fungsi bisa diakses dengan keyboard, sediakan shortcut (Ctrl+R pindai, Ctrl+, pengaturan, F1 bantuan) dan label aksesibilitas untuk screen reader
- Hargai pengaturan "kurangi animasi" dari sistem

Sebelum menulis kode UI, buat wireframe teks sederhana untuk Beranda dan Bersihkan, lalu minta persetujuanku.