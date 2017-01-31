import string
import random
from functools import wraps
from io import BytesIO

import pyqrcode
import requests
from flask import Flask
from flask import render_template
from flask import request
from flask import redirect
from flask import flash
from flask import url_for
from flask import abort

from leancloud import User
from leancloud import Object
from leancloud import GeoPoint
from leancloud import File
from leancloud import LeanCloudError
from geolite2 import geolite2


app = Flask(__name__)


Visits = Object.extend('Visits')
Shortened = Object.extend('Shortened')
URL_KEY_SIZE = 4


class QRCode(File):
    pass


class SniffUser(User):
    pass


def login_required(func):
    @wraps(func)
    def secret_view(*args, **kwargs):
        current_user = SniffUser.get_current()
        if not current_user:
            abort(401)
        else:
            return func(*args, **kwargs)
    return secret_view


@app.route('/login')
def login_form():
    return render_template('login.html')


@app.route('/login', methods=['POST'])
def login():
    username, password = request.form['username'], request.form['password']
    sniffer = SniffUser()
    try:
        sniffer.login(username, password)
        flash('logged in', 'success')
        return redirect(url_for('url_shortener_form'))
    except LeanCloudError as e:
        flash(e.error, 'danger')
        return redirect(url_for('login_form'))


@app.route('/logout')
@login_required
def logout():
    current_user = SniffUser.get_current()
    current_user.logout()
    flash('logged out', 'info')
    return redirect(url_for('login'))


@app.errorhandler(401)
def unauthorized(e):
    return render_template('401.html'), 401


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/url_shortener')
@login_required
def url_shortener_form():
    return render_template('shortener.html')


@app.route('/url_shortener', methods=['POST'])
@login_required
def url_shortener():
    shortened = None
    lurl = request.form['url']
    if request.url_root in lurl:
        flash('Big brother is not to be watched.', 'info')
        return redirect(url_for('url_shortener_form'))
    try:
        if url_is_dead(lurl):
            flash('Given URL is dead.', 'danger')
        else:
            shortened = gen_short_url(lurl)
    except (requests.exceptions.InvalidSchema, requests.exceptions.MissingSchema) as e:
        flash('Please enter an URL with valid schema. e.g: http://, https://.', 'danger')
    return render_template('shortener.html', shortened=shortened, host=request.url_root)


@app.route('/<surl>')
def go(surl):
    target = get_long(surl)
    if target is None:
        abort(404)
    visit = Visits()
    visit.set('target', target)
    ip_address = request.headers.get('x-real-ip')
    if ip_address:
        geo_info = get_geo_info(ip_address)
        visit.set(geo_info)
    visit.set('ip_address', ip_address)
    browser_info = {
        'browser': 'weixin' if 'MicroMessenger' in request.user_agent.string else request.user_agent.browser,
        'browser_version': request.user_agent.version,
        'platform': request.user_agent.platform,
        'language': request.user_agent.language
    }
    visit.set(browser_info)
    campaign_info = {
        'campaign': request.args.get('utm_campaign'),
        'campaign_source': request.args.get('utm_source'),
        'campaign_medium': request.args.get('utm_medium'),
        'campaign_term': request.args.get('utm_term'),
        'campaign_content': request.args.get('utm_content')
    }
    visit.set(campaign_info)
    visit.save()
    return redirect(get_long(surl).get('long'))


def url_is_dead(url: str) -> bool:
    """Check URL's availability.

    Args:
        url: The URL string to be checked.

    Returns:
        True for URL not available, False otherwise.
    """
    res = requests.head(url)
    if res.status_code >= 400:
        return True
    elif res.status_code < 400:
        return False


def gen_random_string(size: int) -> str:
    """Generates a random string of given length.

    Args:
        size: The length of the desired random string.

    Returns:
        random_string: A string constructed with random ascii letters and digits.
    """
    random_string = ''.join(random.choice(string.ascii_letters + string.digits) for x in range(size))
    try:
        shortened = Shortened.query.equal_to('short', random_string).first()
        return gen_random_string(size + 1)
    except LeanCloudError as e:
        if e.code == 101:
            return random_string
        else:
            raise e


def get_long(surl: str) -> Shortened:
    """Get the source URL for the given URL key if exists."""
    shortened = None
    try:
        shortened = Shortened.query.equal_to('short', surl).first()
    except LeanCloudError as e:
        if e.code == 101:
            lurl = None
        else:
            raise e
    return shortened


def gen_short_url(lurl: str) -> Shortened:
    """Generates the URL key for the given source URL.

    Args:
        lurl: The source URL to be shortened.

    Returns:
        surl: The shortened URL key.
    """
    shortened = Shortened()
    surl = gen_random_string(size=URL_KEY_SIZE)
    shortened.set({"long": lurl, "short": surl})
    try:
        shortened.save()
        return shortened
    except LeanCloudError as e:
        # A unique field was given a value that is already taken
        if e.code == 137:
            existing = Shortened.query.equal_to('long', lurl).first()
            return existing
        else:
            abort(400)


def get_geo_info(ip: str) -> dict:
    """Generates a dictionary contains user's geo info.

    Args:
        ip: IP address.

    Returns:
        geo_info: User's geo location.
    """
    reader = geolite2.reader()
    raw_info = reader.get(ip)
    geo_info = {}
    geo_info['continent'] = raw_info['continent']['names']['en']
    geo_info['country'] = raw_info['country']['names']['en']
    geo_info['subdivisions'] = [x['names']['en'] for x in raw_info['subdivisions']],
    geo_info['city'] = raw_info['city']['names']['en']
    geo_info['location'] = GeoPoint(raw_info['location']['latitude'], raw_info['location']['longitude'])
    return geo_info
