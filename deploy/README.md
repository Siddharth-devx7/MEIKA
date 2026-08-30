# MEIKA — Deploy on a Free Oracle Cloud VPS (systemd)

This is the "actually free forever" path: a 24/7 VPS, no expiry, no credit cap
(the Oracle Cloud free **Always Free** tier). The app runs under **systemd** so
it auto-starts on boot and restarts if it crashes.

---

## Step 1 — Create the free Oracle Cloud account

> Oracle sometimes asks for a credit card even on the free tier to verify
> identity. It will **not** be charged for Always Free resources, but you must
> stay within the free limits (1 free AMD + 1 free ARM VM allowed).

1. Go to https://www.oracle.com/cloud/free/ → **Start for free**.
2. Complete sign-up (email, password, country, and payment verification).
3. Wait for the confirmation email (can take minutes–hours).

## Step 2 — Create the Always Free ARM VM (Ampere A1)

1. In the OCI console, go to **Compute → Instances → Create instance**.
2. **Name**: `meika-vm`
3. **Image**: keep **Canonical Ubuntu** (22.04 or 24.04 LTS).
4. **Shape**: choose **Ampere Arm1 (VM.Standard.A1.Flex)** — this is the free ARM one.
5. **Resources** (stay within Always Free): **2 OCPUs** and **12 GB RAM**.
6. **Networking**: default VCN/subnet is fine.
7. **SSH**: add your **public SSH key** (paste the key from your machine).
8. Click **Create**.

## Step 3 — Allow web traffic (open port 8080)

1. In the console go to **Networking → Virtual cloud networks → (your VCN)**.
2. Open the **Security Lists → Default Security List → Add Ingress Rules**.
3. Add an ingress rule: **Source CIDR** `0.0.0.0/0`, **IP Protocol** `TCP`,
   **Destination Port Range** `8080`, description "gradio web".
4. Save.

> Also open port **22** for SSH if it isn't already (it usually is by default).

## Step 4 — SSH in and get the VM's IP

Find the **Public IP** on the instance page, then from your machine:

```bash
ssh -i ~/.ssh/your_key ubuntu@<PUBLIC_IP>
```

## Step 5 — Run the installer (as root)

```bash
sudo bash -c 'cd / && curl -fsSL https://raw.githubusercontent.com/Siddharth-devx7/MEIKA/master/deploy/install.sh -o /tmp/meika_install.sh && bash /tmp/meika_install.sh'
```

Or, if you just cloned the repo:

```bash
sudo bash deploy/install.sh
```

## Step 6 — Add your real API key

The installer creates `/etc/meika/.env` from the template. Edit it:

```bash
sudo nano /etc/meika/.env
```

Set your Gemini key, then restart:

```bash
sudo systemctl restart meika
sudo systemctl status meika     # should show "active (running)"
```

## Step 7 — Visit your app

Open `http://<PUBLIC_IP>:8080` in a browser.

---

## Day-to-day administration

| Action            | Command                                    |
|-------------------|--------------------------------------------|
| View live logs    | `sudo journalctl -u meika -f`              |
| Restart app       | `sudo systemctl restart meika`             |
| Stop app          | `sudo systemctl stop meika`                |
| Status            | `sudo systemctl status meika`              |
| Update the code   | `cd /opt/meika && sudo git pull && sudo systemctl restart meika` |
| Reinstall deps    | `/opt/meika/venv/bin/pip install -r /opt/meika/requirements.txt` |

---

## Security notes

- The API key lives in `/etc/meika/.env` (owned by root, mode `0600`).
- If you want HTTPS (strongly recommended), either:
  - Put Nginx/Caddy in front as a reverse proxy, or
  - Enable Oracle's free load balancer / WAF (more setup).
- The app binds to `0.0.0.0` because of the ARM environment; restrict it to a
  specific interface or firewall if you only use it internally.
