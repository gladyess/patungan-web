# Patungan

Aplikasi pencatat pengeluaran bersama menggunakan Flask dan SQLite. Peserta dan pengeluaran dapat dikelola, pembagian pengeluaran dapat ditentukan per peserta, dan pembayaran dapat dicentang atau dibatalkan.

## Struktur proyek

```text
Web Alpro/
├── app.py
├── requirements.txt
├── render.yaml
├── README.md
├── templates/
│   └── index.html
├── static/
│   └── styles.css
└── instance/                 # dibuat otomatis; database lokal disimpan di sini
    └── patungan.sqlite3
```

## Menjalankan di Windows

1. Install Python 3.10 atau yang lebih baru dari [python.org](https://www.python.org/downloads/). Saat instalasi, aktifkan **Add Python to PATH**. Di terminal VS Code Windows, launcher `py` juga bisa digunakan.
2. Buka folder proyek ini di VS Code, pilih **Terminal → New Terminal**, lalu jalankan:

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   Jika PowerShell memblokir aktivasi, jalankan `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, lalu aktifkan lagi. Alternatifnya, jangan aktifkan environment dan gunakan `.\.venv\Scripts\python.exe` sebagai pengganti `python` pada langkah berikutnya.

3. Install Flask:

   ```powershell
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

4. Buat secret lokal dan nyalakan aplikasi:

   ```powershell
   $env:SECRET_KEY = (py -c "import secrets; print(secrets.token_hex(32))")
   py app.py
   ```

5. Buka <http://127.0.0.1:5000>. File database otomatis dibuat di `instance/patungan.sqlite3` dan tetap ada setelah aplikasi ditutup.

Jika `py` tidak dikenali, ganti `py` dengan `python` setelah Python ditambahkan ke PATH. Jika perintah `python` menampilkan Microsoft Store, gunakan launcher `py` seperti di atas. Browser memerlukan koneksi internet untuk mengambil Bootstrap, Bootstrap Icons, dan font dari CDN.

## Menjalankan di macOS atau Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python app.py
```

Buka <http://127.0.0.1:5000>.

## Deploy gratis ke Render

1. Push proyek ini ke repository GitHub.
2. Pada Render pilih **New + → Blueprint**, hubungkan repository, dan biarkan Render membaca `render.yaml`.
3. Blueprint mengatur Python, memasang `requirements.txt`, membuat `SECRET_KEY`, dan menjalankan `gunicorn app:app`. Tunggu sampai service berstatus **Live**.
4. Buka URL Render. Tes aplikasi dan endpoint `<URL-RENDER>/health`.

**Penyimpanan Render free:** filesystem service gratis bersifat sementara dan bisa dihapus ketika service di-restart, di-deploy ulang, atau mengalami siklus tertentu. Karena itu database SQLite default bisa kehilangan data. Persistensi disk Render umumnya membutuhkan paket berbayar. Untuk demo, gunakan service free ini; untuk data nyata, pilih layanan dengan penyimpanan persisten atau gunakan PostgreSQL terkelola dan ubah backend database.

## Deploy ke PythonAnywhere

1. Buat akun, kemudian unggah/clone proyek ke `/home/USERNAME/patungan` melalui tab **Files** atau konsol Bash.
2. Di **Consoles → Bash** jalankan:

   ```bash
   cd /home/USERNAME/patungan
   python3.12 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   mkdir -p instance
   ```

3. Buka tab **Web → Add a new web app → Manual configuration → Python 3.12**. Atur virtualenv ke `/home/USERNAME/patungan/.venv`.
4. Buka link **WSGI configuration file**, ganti `USERNAME` di contoh berikut dengan username akun, lalu isi file tersebut:

   ```python
   import os
   import sys

   project_dir = "/home/USERNAME/patungan"
   if project_dir not in sys.path:
       sys.path.insert(0, project_dir)

   os.environ["SECRET_KEY"] = "GANTI_DENGAN_SECRET_ACAK_PANJANG"
   os.environ["DATABASE_PATH"] = os.path.join(project_dir, "instance", "patungan.sqlite3")
   os.environ["FLASK_ENV"] = "production"

   from app import app as application
   ```

   Buat secret lewat console dengan `python -c 'import secrets; print(secrets.token_hex(32))'`, lalu tempel hasilnya pada WSGI configuration. Jangan membagikan secret tersebut.

5. Klik **Reload** di tab Web, lalu kunjungi alamat aplikasi. SQLite disimpan di home directory akun.

## Sebelum dibuka ke publik

- Aplikasi ini belum menyediakan login. Siapa pun yang memiliki URL dapat membaca, mengubah, dan menghapus data; gunakan hanya untuk grup yang memang saling percaya atau tambahkan autentikasi.
- Ganti `SECRET_KEY` dengan secret acak dan rahasiakan.
- Cadangkan database SQLite secara berkala. Jangan menghapus `instance/patungan.sqlite3` jika datanya masih diperlukan.