from flask import Flask, jsonify, request, redirect, session, url_for, flash, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_mail import Mail, Message
from urllib.parse import urlparse
import psycopg2
import uuid
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os

# Charger les variables d'environnement depuis .env
load_dotenv()

# Initialiser Flask
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "une_cle_par_defaut_si_absente")
CORS(app)

# 🔗 Connexion PostgreSQL via DATABASE_URL
db_url = os.getenv("DATABASE_URL")
if not db_url:
    raise RuntimeError("⚠️ DATABASE_URL manquant dans le fichier .env")

parsed_url = urlparse(db_url)

conn = psycopg2.connect(
    dbname=parsed_url.path[1:],  # enlever le "/" initial
    user=parsed_url.username,
    password=parsed_url.password,
    host=parsed_url.hostname,
    port=parsed_url.port
)

# 📦 Créer l'extension unaccent
cur = conn.cursor()
cur.execute("CREATE EXTENSION IF NOT EXISTS unaccent;")
conn.commit()

# ✉️ Configuration Flask-Mail
app.config.update(
    MAIL_SERVER=os.getenv("MAIL_SERVER"),
    MAIL_PORT=int(os.getenv("MAIL_PORT")),
    MAIL_USE_SSL=os.getenv("MAIL_USE_SSL") == "True",
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_DEFAULT_SENDER=(
        os.getenv("MAIL_DEFAULT_SENDER_NAME"),
        os.getenv("MAIL_DEFAULT_SENDER_EMAIL")
    )
)

mail = Mail(app)


# ... Le reste de ton code Flask ...




# Création table
cur.execute("""
CREATE TABLE IF NOT EXISTS utilisateurs (
    id SERIAL PRIMARY KEY,
    nom TEXT NOT NULL,
    prenom TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    numero TEXT NOT NULL,
    motdepasse TEXT NOT NULL,
    inscription_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expiration_date TIMESTAMP,
    role TEXT DEFAULT 'user'
);

CREATE TABLE IF NOT EXISTS admissions (
    id SERIAL PRIMARY KEY,
    nom_universite TEXT NOT NULL,
    pays TEXT NOT NULL,
    ville TEXT,
    programme_disponible TEXT,
    site_web TEXT
);
""")
conn.commit()

cursor = conn.cursor()

# ----------------------
# Inscription
# ----------------------
from werkzeug.security import generate_password_hash
import threading        # pour ne pas bloquer la requête HTTP

def envoyer_mail_async(app, msg):
    """Envoi non bloquant."""
    with app.app_context():
        mail.send(msg)

@app.route("/inscription", methods=["GET", "POST"])
def inscription():
    if request.method == "POST":
        nom        = request.form["nom"]
        prenom     = request.form["prenom"]
        email      = request.form["email"]
        numero     = request.form["numero"]
        motdepasse = request.form["motdepasse"]
        role       = request.form.get("role", "user")

        # 1⃣ hachage avant stockage
        mdp_hash = generate_password_hash(motdepasse)

        try:
            cursor.execute("""
                INSERT INTO utilisateurs (nom, prenom, email, numero, motdepasse, role)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (nom, prenom, email, numero, mdp_hash, role))
            conn.commit()

            # 2⃣ préparation de l'e‑mail
            sujet = "Bienvenue sur CoffreFort 🎉"
            corps = f"""\
Bonjour {prenom} {nom},

Merci de vous être inscrit sur CoffreFort ! cette application vous offre toute les opportunites 
d'admissions universitaires dans le monde.

Vous pouvez dès maintenant vous connecter avec :
    • Adresse : {email}
    • Mot de passe : {motdepasse}

Important : conservez ces informations en lieu sûr.
Bonnes recherches de bourse !

— L’équipe CoffreFort
"""

            msg = Message(subject=sujet, recipients=[email], body=corps)

            # 3⃣ envoi asynchrone
            threading.Thread(target=envoyer_mail_async, args=(app, msg)).start()

            flash("Inscription réussie ! Un e‑mail vient de vous être envoyé.", "success")
            return redirect("/connexion")

        except Exception as e:
            conn.rollback()
            return f"Erreur lors de l'inscription : {e}", 500

    return render_template("inscription.html")

# ----------------------
# Connexion
# ----------------------
from datetime import datetime, timedelta

from werkzeug.security import check_password_hash

@app.route("/connexion", methods=["GET", "POST"])
def connexion():
    if request.method == "POST":
        email       = request.form.get("email")
        motdepasse  = request.form.get("motdepasse")

        cursor.execute(
            "SELECT nom, prenom, motdepasse, role, inscription_date "
            "FROM utilisateurs WHERE email = %s", (email,))
        user = cursor.fetchone()

        if not user:
            return "Utilisateur non trouvé", 404

        nom, prenom, mdp_enregistre, role, inscription_date = user

        # ▶︎ Comparaison correcte
        if check_password_hash(mdp_enregistre, motdepasse):
            maintenant   = datetime.now()
            est_expire   = role == "user" and inscription_date \
                           and (maintenant - inscription_date).total_seconds() > 1800

            if est_expire:
                role = "expiré"

            session.update({
                "nom"       : nom,
                "prenom"    : prenom,
                "email"     : email,
                "role"      : role,
                "start_time": maintenant.strftime("%Y-%m-%d %H:%M:%S")
            })

            if role == "admin":
                return redirect("/admin")
            if role == "premium":
                return redirect("/dashboard")
            if role == "expiré":
                return redirect("/premium")
            return redirect("/dashboard")

        return "Mot de passe incorrect", 401

    return render_template("login.html")


# Affichage
@app.route("/admin/admissions")
def admissions_admin():
    if session.get('role') != 'admin':
        return redirect('/connexion')
    cursor.execute('SELECT * FROM admissions ORDER BY id DESC')
    rows = cursor.fetchall()
    cols = ['id', 'nom_universite', 'pays', 'ville', 'programme_disponible', 'site_web']
    admissions = [dict(zip(cols, r)) for r in rows]
    return render_template('admin_admissions.html', admissions=admissions)

# Ajout / mise à jour
@app.route('/admin/admissions/save', methods=['POST'])
def admissions_save():
    data = request.form
    if data.get('id'):  # update
        cursor.execute("""
            UPDATE admissions
            SET nom_universite=%s, pays=%s, ville=%s,
                programme_disponible=%s, site_web=%s
            WHERE id=%s
        """, (
            data['nom_universite'], data['pays'], data['ville'],
            data['programme_disponible'], data['site_web'], data['id']
        ))
    else:  # insert
        cursor.execute("""
            INSERT INTO admissions (nom_universite, pays, ville, programme_disponible, site_web)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            data['nom_universite'], data['pays'], data['ville'],
            data['programme_disponible'], data['site_web']
        ))
    conn.commit()
    return redirect('/admin/admissions')

# Édition
@app.route('/admin/admissions/<int:aid>/edit')
def admissions_edit(aid):
    cursor.execute('SELECT * FROM admissions WHERE id = %s', (aid,))
    row = cursor.fetchone()
    if not row:
        return "Admission introuvable", 404

    cols = ['id', 'nom_universite', 'pays', 'ville', 'programme_disponible', 'site_web']
    edit = dict(zip(cols, row))

    cursor.execute('SELECT * FROM admissions ORDER BY id DESC')
    admissions = [dict(zip(cols, r)) for r in cursor.fetchall()]
    return render_template('admin_admissions.html', admissions=admissions, edit=edit)

# Suppression
@app.route('/admin/admissions/<int:aid>/delete', methods=['POST'])
def admissions_delete(aid):
    cursor.execute('DELETE FROM admissions WHERE id = %s', (aid,))
    conn.commit()
    return redirect('/admin/admissions')

# ----------------------

import requests
import uuid

@app.route("/paiement", methods=["POST"])
def paiement():
    if "email" not in session:
        return redirect("/connexion")

    transaction_id = str(uuid.uuid4())  # ID unique
    montant = "3000"  # 3000 FCFA ≈ 5$
    description = "Activation Premium CoffreFort"

    payload = {
        "apikey": "2026068600685db8ef452959.23377545",
        "site_id": "105899775",
        "transaction_id": transaction_id,
        "amount": montant,
        "currency": "XOF",
        "description": description,
        "return_url": "http://localhost:5000/dashboard",
        "notify_url": "https://votre-domaine.com/notify",
        "channels": "ALL",
        "customer_name": session.get("nom", "Nom"),
        "customer_email": session.get("email", "test@example.com"),
        "customer_phone_number": "2250101010101",
        "customer_address": "Abidjan",
        "customer_city": "Abidjan",
        "customer_country": "CI"
    }

    headers = {
        "Content-Type": "application/json"
    }

    try:
        res = requests.post("https://api-checkout.cinetpay.com/v2/payment", json=payload, headers=headers)
        res_data = res.json()

        if res_data.get("code") == "201":
            payment_url = res_data["data"]["payment_url"]
            return redirect(payment_url)
        else:
            return "Erreur lors de l'initialisation du paiement", 400
    except Exception as e:
        return f"Erreur de connexion : {e}", 500




@app.route("/notify", methods=["POST"])
def notify_cinetpay():
    data = request.get_json()

    transaction_id = data.get("transaction_id")
    status = data.get("cpm_result")  # "00" = succès
    email = data.get("customer_email")

    if status == "00" and email:
        new_expiration = datetime.now() + timedelta(days=30)
        cursor.execute("""
            UPDATE utilisateurs
            SET role = 'premium', expiration_date = %s
            WHERE email = %s
        """, (new_expiration, email))
        conn.commit()
        print("✅ Paiement confirmé pour :", email)
        return "OK", 200

    print("❌ Paiement échoué ou email manquant")
    return "KO", 400



# -------------------------------------------------------
# /dashboard – affiche premium ou minuteur free 30 min
# -------------------------------------------------------
from datetime import datetime, timedelta
from flask import session, redirect, render_template

@app.route("/dashboard")
def dashboard():
    if "email" not in session:
        return redirect("/connexion")

    email = session["email"]

    # Lire les données utilisateur à jour
    cursor.execute("""
        SELECT nom, prenom, role, expiration_date
        FROM utilisateurs
        WHERE email=%s
    """, (email,))
    user = cursor.fetchone()

    if not user:
        session.clear()
        return redirect("/connexion")

    nom, prenom, role, expiration_db = user

    now = datetime.now()

    # Cas ADMIN (pas concerné par les restrictions)
    if role == "admin":
        return render_template("dashboard.html",
                               nom=nom, prenom=prenom,
                               minutes_left=None,
                               show_modal=False)

    # Cas PREMIUM avec date valide
    if role == "premium" and expiration_db and expiration_db > now:
        return render_template("dashboard.html",
                               nom=nom, prenom=prenom,
                               minutes_left=None,
                               show_modal=False)

    # Sinon, gérer les 30 minutes gratuites
    # Si start_time absent (ex : fermeture navigateur)
    if "start_time" not in session:
        session["start_time"] = now.strftime("%Y-%m-%d %H:%M:%S")

    start_time = datetime.strptime(session["start_time"], "%Y-%m-%d %H:%M:%S")
    elapsed = now - start_time
    remaining = timedelta(minutes=30) - elapsed
    minutes_left = max(0, int(remaining.total_seconds() // 60))
    show_modal = elapsed > timedelta(minutes=30)

    return render_template("dashboard.html",
                           nom=nom, prenom=prenom,
                           minutes_left=minutes_left,
                           show_modal=show_modal)

# Dashboard Admin
# ----------------------
@app.route("/admin")
def admin():
    if session.get("role") != "admin":
        return redirect("/connexion")

    cursor.execute("""
        SELECT id, nom, prenom, email, role, COALESCE(
            TO_CHAR(expiration_date, 'YYYY-MM-DD'), '') AS exp
        FROM utilisateurs
        ORDER BY inscription_date DESC
    """)
    rows = cursor.fetchall()

    # map à des dicts pour plus de lisibilité
    cols = ["id", "nom", "prenom", "email", "role", "expiration_date"]
    utilisateurs = [dict(zip(cols, r)) for r in rows]

    return render_template("admin.html", utilisateurs=utilisateurs)



@app.route('/inscription')
def inscris():
    return render_template ("inscription.html")

@app.route('/connexion')
def connect():
    return render_template("login.html")

@app.route('/home')
def home():
    return render_template("home.html")

# ----------------------
# Premium Page
# ----------------------
@app.route('/premium')
def premium():
    return render_template ("paiement.html")

@app.route("/parametres", methods=["GET", "POST"])
def parametres():
    if request.method == "POST":
        session["params"] = {
            "pays_pref": request.form.get("pays_pref"),
            "niveau": request.form.get("niveau"),
            "domaine": request.form.get("domaine"),
            "langue": request.form.get("langue"),
            "notifications": request.form.get("notifications")
        }
        return redirect("/dashboard")  # redirige vers ton tableau de bord ou autre

    params = session.get("params", {})
    return render_template("parametres.html", params=params)


import json
from flask import request, render_template

# ------------------  ROUTE  ------------------
from flask import request, session, render_template
import unicodedata

def remove_accents(txt):
    return ''.join(c for c in unicodedata.normalize('NFD', txt) if unicodedata.category(c) != 'Mn')

@app.route("/universites", methods=["GET"])
def universites():
    """
    Renvoie la liste des universités depuis la table `admissions`.
    Filtre par pays si précisé, sans accent ni casse.
    """
    pays_param = request.args.get("pays", "").strip().lower()
    if not pays_param:
        prefs = session.get("params", {})
        pays_param = (prefs.get("pays_pref") or "").strip().lower()

    pays_param = remove_accents(pays_param)  # 🔥 suppression accents

    # 🔎 Requête SQL
    if pays_param:
        cursor.execute("""
            SELECT nom_universite, pays, ville, programme_disponible, site_web
            FROM admissions
            WHERE LOWER(unaccent(pays)) LIKE %s
            ORDER BY nom_universite
        """, (f"%{pays_param}%",))
    else:
        cursor.execute("""
            SELECT nom_universite, pays, ville, programme_disponible, site_web
            FROM admissions
            ORDER BY pays, nom_universite
            LIMIT 100
        """)

    rows = cursor.fetchall()
    cols = ["nom_universite", "pays", "ville", "programme_disponible", "site_web"]
    universites = [dict(zip(cols, r)) for r in rows]

    return render_template("dashboard.html", universites=universites, pays=pays_param)

#outes « actions » (Activer, Désactiver, Supprimer) cote' admin .

@app.route("/admin/<int:user_id>/activate", methods=["POST"])
def admin_activate(user_id):
    cursor.execute("""
        UPDATE utilisateurs
        SET role='premium',
            expiration_date = NOW() + INTERVAL '30 days'
        WHERE id=%s
    """, (user_id,))
    conn.commit()
    return redirect("/admin")

@app.route("/admin/<int:user_id>/deactivate", methods=["POST"])
def admin_deactivate(user_id):
    cursor.execute("""
        UPDATE utilisateurs
        SET role='user',
            expiration_date = NULL
        WHERE id=%s
    """, (user_id,))
    conn.commit()
    return redirect("/admin")

@app.route("/admin/<int:user_id>/delete", methods=["POST"])
def admin_delete(user_id):
    cursor.execute("DELETE FROM utilisateurs WHERE id=%s", (user_id,))
    conn.commit()
    return redirect("/admin")






# ----------------------
# Déconnexion
# ----------------------

@app.route("/parametres")
def parametres1():
    return redirect("/parametres.html")


@app.route("/deconnexion")
def deconnexion():
    session.clear()
    return redirect("/connexion")


