'''
    This was my 1st try to deploy a Telegram chatbot with webhooks, integrated to Flask-app (unsuccessfull)
'''


# -*- coding: utf-8 -*-

import os
from flask import Flask, render_template, url_for, request, redirect, flash, session, make_response, jsonify
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, TextAreaField, PasswordField, BooleanField
from wtforms.validators import DataRequired, Length, URL, Email, Optional
from flask_wtf.csrf import CSRFProtect
from flask_wtf.recaptcha import RecaptchaField
from flask_mail import Mail, Message
from pymongo import MongoClient
from passlib.hash import sha256_crypt
from flask_jsglue import JSGlue
from flask_googlemaps import GoogleMaps
from flask_googlemaps import Map
from flask_babel import Babel, gettext
import twitter
import uuid
import telebot
import requests, json
import time
import chatbot_markup
from werkzeug.utils import secure_filename
import datetime
from datetime import timezone

from keys import FLASK_SECRET_KEY, RECAPTCHA_PRIVATE_KEY, GOOGLE_MAPS_API_KEY, TWITTER_CONSUMER_KEY, TWITTER_CONSUMER_SECRET, TWITTER_ACCESS_TOKEN_KEY, TWITTER_ACCESS_TOKEN_SECRET, MAIL_PWD, TG_TOKEN, DF_TOKEN,

print(' ')
print('########### chatbot.py - new session ############')

import tg_functions
RECAPTCHA_PUBLIC_KEY = '6LdlTE0UAAAAACb7TQc6yp12Klp0fzgifr3oF-BC'
SITE_URL = 'https://fellowtraveler.club'
LANGUAGES = {
    'en': 'English',
    'ru': 'Русский',
    'de': 'Deutsch',
    'fr': 'Français'
}

app = Flask(__name__)
app.config.from_object(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 20 # max photo to upload is 20Mb
csrf = CSRFProtect(app)
csrf.init_app(app)
app.secret_key = FLASK_SECRET_KEY
GoogleMaps(app, key=GOOGLE_MAPS_API_KEY)
jsglue = JSGlue(app)
babel = Babel(app)
twitter_api = twitter.Api(consumer_key=TWITTER_CONSUMER_KEY, consumer_secret=TWITTER_CONSUMER_SECRET, access_token_key=TWITTER_ACCESS_TOKEN_KEY, access_token_secret=TWITTER_ACCESS_TOKEN_SECRET)
bot = telebot.TeleBot(TG_TOKEN)

mail = Mail(app)
app.config.update(
    DEBUG=True,
    MAIL_SERVER='smtp.gmail.com',
    MAIL_PORT=587,
    MAIL_USE_SSL=False,
    MAIL_USE_TLS=True,
    MAIL_USERNAME = 'mailvulgaris@gmail.com',
    MAIL_PASSWORD = MAIL_PWD
)
mail = Mail(app)

####################################### TG Bot INI START #######################################

OURTRAVELLER = 'Teddy'
PHOTO_DIR = 'static/uploads/'
SHORT_TIMEOUT = 0  # 2 # seconds, between messages for imitation of 'live' typing
MEDIUM_TIMEOUT = 0  # 4
LONG_TIMEOUT = 0  # 6

CONTEXTS = []   # holds last state
NEWLOCATION = {    # stores data for traveler's location before storing it to DB
    'author': None,
    'channel': 'Telegram',
    'user_id_on_channel': None,
    'longitude': None,
    'latitude': None,
    'formatted_address': None,
    'locality': None,
    'administrative_area_level_1': None,
    'country': None,
    'place_id': None,
    'comment': None,
    'photos': []
}

####################################### TG Bot INI END #########################################

################################# TG Bot Webhook INI START #####################################

WEBHOOK_HOST = 'fellowtraveler.club'
WEBHOOK_PORT = 8443  # 443, 80, 88 or 8443 (port need to be 'open')
WEBHOOK_LISTEN = '0.0.0.0'  # In some VPS you may need to put here the IP addr

WEBHOOK_SSL_CERT = '/etc/letsencrypt/live/fellowtraveler.club/fullchain.pem'  # Path to the ssl certificate
WEBHOOK_SSL_PRIV = '/etc/letsencrypt/live/fellowtraveler.club/privkey.pem'  # Path to the ssl private key

WEBHOOK_URL_BASE = "https://%s:%s" % (WEBHOOK_HOST, WEBHOOK_PORT)
WEBHOOK_URL_PATH = "/%s/" % (TG_TOKEN)
################################# TG Bot Webhook INI END #######################################

class WhereisTeddyNow(FlaskForm):
    author = StringField(gettext('Your name'), validators=[Length(-1, 50, gettext('Your name is a bit too long (50 characters max)'))])
    comment = TextAreaField(gettext('Add a comment'), validators=[Length(-1, 280, gettext('Sorry but comments are uploaded to Twitter and thus can\'t be longer than 280 characters'))])
    email4updates = StringField(gettext('Get updates by email'),
                               validators=[Optional(), Email(gettext('Please enter a valid e-mail address'))])
    secret_code = PasswordField(gettext('Secret code from the toy (required)'), validators=[DataRequired(gettext('Please enter the code which you can find on the label attached to the toy')),
                              Length(6, 6, gettext('Secret code must have 6 digits'))])
    #recaptcha = RecaptchaField()
    submit = SubmitField(gettext('Submit'))

class HeaderEmailSubscription(FlaskForm):
    email4updates = StringField(gettext('Get updates by email:'), validators=[Optional(), Email(gettext('Please enter a valid e-mail address'))])
    emailsubmit = SubmitField(gettext('Subscribe'))

def save_subscriber(email_entered):
    '''
        Gets email address, checks if it's not already in subscribers' DB, saves it, sends a verification email and informs user with flashes
        Function not moved to function file not to move flask_mail setup block
    '''
    try:
        # Check if user's email is not already in DB
        client = MongoClient()
        db = client.TeddyGo
        subscribers = db.subscribers
        email_already_submitted = subscribers.find_one({"$and": [{"email": email_entered}, {'unsubscribed': {'$ne': True}}]})

        if email_already_submitted:
            if email_already_submitted['verified']:
                flash(gettext("Email {} is already subscribed and verified".format(email_entered)), 'header')
                return {"status": "error", "message": "Email {} is already subscribed and verified".format(email_entered)}
            else:
                flash(gettext("Email {} is already subscribed but has not been verified yet".format(email_entered)), 'header')
                return {"status": "error",
                        "message": "Email {} is already subscribed but has not been verified yet".format(email_entered)}

        user_locale = get_locale()

        userid = str(uuid.uuid4())

        new_subscriber = {
            "email": email_entered,
            "locale": user_locale,
            "verified": False,
            "verification_code": sha256_crypt.encrypt(userid),
            "unsubscribed": None
        }

        verification_link = '{}/verify/{}/{}'.format(SITE_URL, email_entered, userid)
        unsubscription_link = '{}/unsubscribe/{}/{}'.format(SITE_URL, email_entered, userid)

        new_subscriber_id = subscribers.insert_one(new_subscriber).inserted_id

        msg = Message("Fellowtraveler.club: email verification link",
                      sender="mailvulgaris@gmail.com", recipients=[email_entered])
        msg.html = "Hi!<br><br>Thanks for subscribing to Teddy's location updates!<br>They won't be too often (not more than once a week).<br><br>Please verify your email address by clicking on the following link:<br><b><a href='{}' target='_blank'>{}</a></b><br><br>If for any reason later you will decide to unsubscribe, please click on the following link:<br><a href='{}' target='_blank'>{}</a>".format(verification_link, verification_link, unsubscription_link, unsubscription_link)
        mail.send(msg)

        flash(gettext("A verification link has been sent to your email address. Please click on it to verify your email"), 'header')
        return {"status": "success",
                "message": "A verification link has been sent to your email address. Please click on it to verify your email"}
    except Exception as error:
        flash(gettext("Error happened ('{}')".format(error)), 'header')
        return {"status": "error",
                "message": "Error happened ('{}')".format(error)}

@babel.localeselector
def get_locale():
    user_language = request.cookies.get('UserPreferredLanguage')
    print("user_language: {}".format(user_language))
    print("autodetect_language: {}".format(request.accept_languages.best_match(LANGUAGES.keys())))
    if user_language != None:
        return user_language
    else:
        return request.accept_languages.best_match(LANGUAGES.keys())

# Process webhook calls
@app.route(WEBHOOK_URL_PATH, methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    else:
        abort(403)

@app.route('/index/', methods=['GET', 'POST'])
@app.route('/', methods=['GET', 'POST']) # later index page will aggragate info for several travellers
#@app.route('/teddy/', methods=['GET', 'POST'])
@csrf.exempt
def index():
    print('Index!')
    try:
        traveller = 'Teddy'
        whereisteddynowform = WhereisTeddyNow()
        subscribe2updatesform = HeaderEmailSubscription()

        # POST-request
        if request.method == 'POST':
            print('Index-Post')

            # Get travellers history
            whereteddywas = tg_functions.get_location_history(traveller)
            locations_history = whereteddywas['locations_history']

            # Prepare a map
            teddy_map = Map(
                identifier="teddy_map",
                lat=whereteddywas['start_lat'],
                lng=whereteddywas['start_long'],
                zoom=8,
                language="en",
                style="height:480px;width:720px;margin:1;",
                markers=whereteddywas['mymarkers'],
                fit_markers_to_bounds = True
            )

            # Check for preferred language
            user_language = get_locale()

            # Check if user entered some location (required parameter) (data is passed from jQuery to Flask and
            # saved in session
            if 'geodata' not in session:
                print('Here1')
                flash(gettext('Please enter Teddy\'s location (current or on the photo)'),
                        'addlocation')
                print('No data in session!')
                return render_template('index.html', whereisteddynowform=whereisteddynowform, subscribe2updatesform=subscribe2updatesform,
                                       locations_history=locations_history, teddy_map=teddy_map, language=user_language)

            # Get user's input
            print('Here2')
            if whereisteddynowform.validate_on_submit():
                print('Here4')
                # Get user's input
                author = whereisteddynowform.author.data
                if author == '':
                    author = gettext("Anonymous")
                #location = whereisteddynowform.location.data
                comment = whereisteddynowform.comment.data
                secret_code = whereisteddynowform.secret_code.data
                email_entered = whereisteddynowform.email4updates.data
                if email_entered != '':
                    save_subscriber(email_entered)

                # Get photos (4 at max)
                photos = request.files.getlist('photo')
                photos_list = []
                for n in range(len(photos)):
                    if n<4:
                        path = tg_functions.photo_check_save(photos[n])
                        if path != 'error':
                            photos[n].save(os.path.join(app.static_folder, path))
                            photos_list.append(path)
                        else:
                            # At least one of images is invalid. Messages are flashed from photo_check_save()
                            return render_template('index.html', whereisteddynowform=whereisteddynowform, subscribe2updatesform=subscribe2updatesform, locations_history=locations_history, teddy_map=teddy_map, language=user_language)
                if len(photos)>4:
                    flash(
                        gettext('Comments are uploaded to Twitter and thus can\'t have more than 4 images each. Only the first 4 photos were uploaded'),
                        'addlocation')

                # Save data to DB
                # Connect to DB 'TeddyGo'
                client = MongoClient()
                db = client.TeddyGo

                # Check secret code in collection 'travellers'
                collection_travellers = db.travellers
                teddys_sc_should_be = collection_travellers.find_one({"name": 'Teddy'})['secret_code']
                if not sha256_crypt.verify(secret_code, teddys_sc_should_be):
                    flash(gettext('Invalid secret code'), 'addlocation')
                    return render_template('index.html', whereisteddynowform=whereisteddynowform, subscribe2updatesform=subscribe2updatesform, locations_history=locations_history, teddy_map=teddy_map, language=user_language)
                else:
                    # Prepare dictionary with new location info
                    geodata = session['geodata']

                    new_teddy_location = {
                        'author': author,
                        'channel': 'website',
                        'user_id_on_channel': None, # for website entry
                        'longitude': float(geodata.get('longitude')),
                        'latitude': float(geodata.get('latitude')),
                        'formatted_address': geodata.get('formatted_address'),
                        'locality':  geodata.get('locality'),
                        'administrative_area_level_1':  geodata.get('administrative_area_level_1'),
                        'country':  geodata.get('country'),
                        'place_id':  geodata.get('place_id'),
                        'comment': comment,
                        'photos': photos_list
                    }

                    # Connect to collection and insert document
                    collection_teddy = db[traveller]
                    new_teddy_location_id = collection_teddy.insert_one(new_teddy_location).inserted_id
                    print('new_teddy_location_id: {}'.format(new_teddy_location_id))

                    # Update journey summary
                    tg_functions.summarize_journey('Teddy')

                    # Post to Twitter
                    '''
                    newstatus = 'Teddy with {} in {}'.format(new_teddy_location['author'], new_teddy_location['formatted_address'])
                    if comment != '':
                        newstatus += '. {} wrote: {}'.format(new_teddy_location['author'], new_teddy_location['comment'])
                    status = twitter_api.PostUpdate(status=newstatus, media=new_teddy_location['photos'], latitude=new_teddy_location['latitude'],
                                                    longitude=new_teddy_location['longitude'], display_coordinates=True)
                    print(status.text)
                    '''

                    # Clear data from session
                    session.pop('geodata', None)

                    # Get travellers history
                    whereteddywas = tg_functions.get_location_history(traveller)
                    locations_history = whereteddywas['locations_history']

                    # Prepare a map
                    teddy_map = Map(
                        identifier="teddy_map",
                        lat=whereteddywas['start_lat'],
                        lng=whereteddywas['start_long'],
                        zoom=8,
                        language="en",
                        style="height:480px;width:720px;margin:1;",
                        markers=whereteddywas['mymarkers'],
                        fit_markers_to_bounds=True
                    )

                    # Check for preferred language
                    user_language = get_locale()

                    return render_template('index.html', whereisteddynowform=whereisteddynowform, subscribe2updatesform=subscribe2updatesform,
                                           locations_history=locations_history, teddy_map=teddy_map,
                                           language=user_language)
            else:
                print('Here3')
                # Clear data from session
                session.pop('geodata', None)

                return render_template('index.html', whereisteddynowform=whereisteddynowform, subscribe2updatesform=subscribe2updatesform, locations_history=locations_history, teddy_map=teddy_map, language=user_language)

        # GET request
        # Get travellers history (will be substituted with timeline embedded from Twitter )
        print('Index-Get')

        # Flashing disclaimer message
        disclaimer_shown = request.cookies.get('DisclaimerShown')
        print("$$$$$$$ {}".format(disclaimer_shown))
        if not disclaimer_shown:
            flash(gettext(
                'No, it\'s not a trick and supposed to be safe but please see <a href="">disclaimer</a>'),
                  'header')
            # set a cookie so that disclaimer will be shown only once
            expire_date = datetime.datetime.now()
            expire_date = expire_date + datetime.timedelta(days=90)
            redirect_to_index = redirect('/')
            response = app.make_response(redirect_to_index)
            response.set_cookie('DisclaimerShown', 'yes', expires=expire_date)
            print('setting cookie')
            print(str(response))
            return response

        # Get travellers history (will be substituted with timeline embedded from Twitter )
        whereteddywas = tg_functions.get_location_history(traveller)
        print('whereteddywas!')
        locations_history = whereteddywas['locations_history']
        print('locations_history!')

        # Prepare a map
        teddy_map = Map(
            identifier="teddy_map",
            lat=whereteddywas['start_lat'],
            lng=whereteddywas['start_long'],
            zoom=8,
            language="en",
            style="height:480px;width:700px;margin:1;",
            markers=whereteddywas['mymarkers'],
            fit_markers_to_bounds=True
        )
        print('teddy_map!')

        # Check for preferred language
        user_language = get_locale()
        return render_template('index.html', whereisteddynowform=whereisteddynowform, subscribe2updatesform=subscribe2updatesform, locations_history=locations_history, teddy_map=teddy_map, language=user_language)

    except Exception as error:
        print("error: {}".format(error))
        return render_template('error.html', error=error, subscribe2updatesform=subscribe2updatesform)

@app.route("/get_geodata_from_gm", methods=["POST"])
@csrf.exempt
def get_geodata_from_gm():
    print("get_geodata_from_gm!")
    if request.method == "POST":
        mygeodata = request.get_json()
        # Retrieve 1) formatted address, 2) latitude, 3) longitude, 4) ['locality', 'political'] (~town), 5) ['administrative_area_level_1', 'political'] (~region/state), 6) ['country', 'political'] (country) and 7) place ID
        if mygeodata[0]:
            formatted_address = mygeodata[0].get('formatted_address')
            latitude = mygeodata[0].get('geometry').get('location').get('lat', 0) # unlikely that it will be in 0lat 0 long (somewhere in Atlantic Ocean)
            longitude = mygeodata[0].get('geometry').get('location').get('lng', 0)
            address_components = mygeodata[0].get('address_components')
            locality, administrative_area_level_1, country, place_id = None, None, None, None
            for address_component in address_components:
                types = address_component.get('types')
                short_name = address_component.get('short_name')
                #print("type: {}, short name: {}".format(types, short_name))
                if 'locality' in types:
                    locality = short_name
                elif 'administrative_area_level_1' in types:
                    administrative_area_level_1 = short_name
                elif 'country' in types:
                    country = short_name
            place_id = mygeodata[0].get('place_id')

        parsed_geodata = {
            'latitude': latitude,
            'longitude': longitude,
            'formatted_address': formatted_address,
            'locality': locality,
            'administrative_area_level_1': administrative_area_level_1,
            'country': country,
            'place_id': place_id
        }
        #print('Geodata: {}'.format(parsed_geodata))
        session['geodata'] = parsed_geodata
    return 'Geodata saved to session'

@app.route("/language/<lang_code>/")
@csrf.exempt
def user_language_to_coockie(lang_code):
    expire_date = datetime.datetime.now()
    expire_date = expire_date + datetime.timedelta(days=90)
    redirect_to_index = redirect('/')
    response = app.make_response(redirect_to_index)
    response.set_cookie('UserPreferredLanguage', lang_code, expires=expire_date)
    print('Preferred language, {}, was saved to coockie'.format(lang_code.upper()))
    return response

@app.route("/subscribe", methods=["POST"])
@csrf.exempt
def direct_subscription():
    subscribe2updatesform = HeaderEmailSubscription()
    if request.method == "POST" and subscribe2updatesform.validate_on_submit():
        print("Email entered: {}".format(subscribe2updatesform.email4updates.data))
        email_entered = subscribe2updatesform.email4updates.data
    else:
        flash(gettext("Please enter a valid e-mail address"), 'header')
        return redirect(url_for('index'))
    result = save_subscriber(email_entered)
    return redirect(url_for('index'))

@app.route("/verify/<user_email>/<verification_code>")
@csrf.exempt
def verify_email(user_email, verification_code):
    try:
        # Check if user's email exists in DB and is not unsubscribed
        client = MongoClient()
        db = client.TeddyGo
        subscribers = db.subscribers
        email_already_submitted = subscribers.find_one(
            {"$and": [{"email": user_email}, {'unsubscribed': {'$ne': True}}]})
        if not email_already_submitted:
            flash(gettext("Email {} was not found".format(user_email)), 'header')
            return redirect(url_for('index'))

        # Find sha256_crypt-encrypted verification code in DB for a given user_email
        docID = subscribers.find_one(
            {"$and": [{"email": user_email}, {'unsubscribed': {'$ne': True}}]}).get('_id')
        print("##### docID: {}".format(docID))
        print("##### Verif code: {}".format(subscribers.find_one({'_id': docID})['verification_code']))
        print("##### Verif code should be: {}".format(verification_code))

        verification_code_should_be = subscribers.find_one({'_id': docID})['verification_code']

        # Compare it with the code submitted
        if not sha256_crypt.verify(verification_code, verification_code_should_be):
            # If invalid code - inform user
            flash(gettext('Sorry but you submitted an invalid verification code. Email address not verified'), 'header')
            return redirect(url_for('index'))
        else:
            # If code Ok, check if email is not already verified
            if subscribers.find_one({'_id': docID})['verified'] == True:
                flash(gettext('Email address {} already verified'.format(user_email)), 'header')
                return redirect(url_for('index'))
            else:
                # update the document in DB and inform user
                subscribers.update_one({'_id': docID}, {'$set': {'verified': True, 'unsubscribed': False}})
                flash(gettext('Email verified! Thanks for subscribing to Teddy\'s location updates!'), 'header')
                return redirect(url_for('index'))
    except Exception as error:
        flash(gettext("Error happened ('{}')".format(error)), 'header')
        return redirect(url_for('index'))

@app.route("/unsubscribe/<user_email>/<verification_code>")
@csrf.exempt
def unsubscribe(user_email, verification_code):
    try:
        # Check if user's email exists in DB
        client = MongoClient()
        db = client.TeddyGo
        subscribers = db.subscribers
        email_already_submitted = subscribers.find_one(
            {"$and": [{"email": user_email}, {'unsubscribed': {'$ne': True}}]})
        if not email_already_submitted:
            flash(gettext("Email {} was not found".format(user_email)), 'header')
            return redirect(url_for('index'))

        # Find sha256_crypt-encrypted verification code in DB for a given user_email
        docID = subscribers.find_one(
            {"$and": [{"email": user_email}, {'unsubscribed': {'$ne': True}}]}).get('_id')
        verification_code_should_be = subscribers.find_one({'_id': docID})['verification_code']


        # Compare it with the code submitted
        if not sha256_crypt.verify(verification_code, verification_code_should_be):
            # If invalid code - inform user
            flash(gettext('Sorry but you submitted an invalid verification code. Unsubscription failed'), 'header')
            return redirect(url_for('index'))
        else:
            # If code Ok, "soft"-delete the document
            subscribers.update_one({'_id': docID}, {'$set': {'unsubscribed': True}})
            flash(gettext('Email successfully unsubscribed'), 'header')
            return redirect(url_for('index'))
    except Exception as error:
        flash(gettext("Error happened ('{}')".format(error)), 'header')
        return redirect(url_for('index'))

@app.errorhandler(404)
@csrf.exempt
def page_not_found(error):
    subscribe2updatesform = HeaderEmailSubscription()
    return render_template('404.html', subscribe2updatesform=subscribe2updatesform), 404

@app.errorhandler(413)
@csrf.exempt
def file_too_large(error):
    subscribe2updatesform = HeaderEmailSubscription()
    return render_template('413.html', subscribe2updatesform=subscribe2updatesform), 413

@app.route('/webhook', methods=['POST'])
@csrf.exempt
def webhook():
    # Get request parameters
    req = request.get_json(silent=True, force=True)
    action = req.get('result').get('action')

    # TeddyGo - show timeline
    if action == "teddygo_show_timeline":
        location_iteration = tg_functions.show_location('Teddy', req)
        ourspeech = location_iteration['payload']
        output_context = location_iteration['updated_context']
        res = tg_functions.make_speech(ourspeech, action, output_context)

    else:
        # If the request is not of our actions throw an error
        res = {
            'speech': 'Something wrong happened',
            'displayText': 'Something wrong happened'
        }
    return make_response(jsonify(res))

###################################### '/' Handlers START ######################################

@bot.message_handler(commands=['start'])
# Block 0
def start_handler(message):
    global CONTEXTS
    # A fix intended not to respond to every image uploaded (if several)
    if 'last_input_media' in CONTEXTS:
        CONTEXTS.remove('last_input_media')
        CONTEXTS.remove('media_input')

    if 'if_journey_info_needed' not in CONTEXTS:
        CONTEXTS.clear()
        bot.send_message(message.chat.id, 'Hello, {}!'.format(message.from_user.first_name))
        time.sleep(SHORT_TIMEOUT)
        travelers_story_intro(message.chat.id)
        if 'if_journey_info_needed' not in CONTEXTS:
            CONTEXTS.append('if_journey_info_needed')
    else:
        travelers_story_intro(message.chat.id)
        if 'if_journey_info_needed' not in CONTEXTS:
            CONTEXTS.append('if_journey_info_needed')
    # Console logging
    print()
    print('User entered "/start"')
    print('Contexts: {}'.format(CONTEXTS))

@bot.message_handler(commands=['tell_your_story'])
def tell_your_story(message):
    global CONTEXTS
    # A fix intended not to respond to every image uploaded (if several)
    if 'last_input_media' in CONTEXTS:
        CONTEXTS.remove('last_input_media')
        CONTEXTS.remove('media_input')

    travelers_story_intro(message.chat.id)
    if 'if_journey_info_needed' not in CONTEXTS:
        CONTEXTS.append('if_journey_info_needed')
    # Console logging
    print()
    print('User entered "/tell_your_story"')
    print('Contexts: {}'.format(CONTEXTS))

@bot.message_handler(commands=['help'])
def help(message):
    global CONTEXTS
    # A fix intended not to respond to every image uploaded (if several)
    if 'last_input_media' in CONTEXTS:
        CONTEXTS.remove('last_input_media')
        CONTEXTS.remove('media_input')

    get_help(message.chat.id)
    # Console logging
    print()
    print('User entered "/help"')
    print('Contexts: {}'.format(CONTEXTS))


@bot.message_handler(commands=['you_got_fellowtraveler'])
def you_got_fellowtraveler(message):
    global CONTEXTS
    # A fix intended not to respond to every image uploaded (if several)
    if 'last_input_media' in CONTEXTS:
        CONTEXTS.remove('last_input_media')
        CONTEXTS.remove('media_input')

    if 'code_correct' not in CONTEXTS:
        bot.send_message(message.chat.id, 'Oh, that\'s a tiny adventure and some responsibility ;)\nTo proceed please <b>enter the secret code</b> from the toy', parse_mode='html')
        bot.send_photo(message.chat.id, 'https://iuriid.github.io/img/ft-3.jpg', reply_markup=chatbot_markup.cancel_help_contacts_menu)
    # Console logging
    print()
    print('User entered "/you_got_fellowtraveler"')
    print('Contexts: {}'.format(CONTEXTS))


###################################### '/' Handlers END ######################################

################################### 'Custom' handlers START ##################################


@bot.message_handler(content_types=['text'])
# Handling all text input (NLP using Dialogflow and then depending on recognised intent and contexts variable
def text_handler(message):
    global CONTEXTS
    # A fix intended not to respond to every image uploaded (if several)
    if 'last_input_media' in CONTEXTS:
        CONTEXTS.remove('last_input_media')
        CONTEXTS.remove('media_input')

    # Get input data
    users_input = message.text
    chat_id = message.chat.id
    from_user = message.from_user

    # And pass it to the main handler function [main_hadler()]
    main_handler(users_input, chat_id, from_user, is_btn_click=False, geodata=None, media=False)


@bot.callback_query_handler(func=lambda call: True)
# Handling clicks on different InlineKeyboardButtons
def button_click_handler(call):
    # All possible buttons (10)
    # Yes | No, thanks | Cancel | Help | You got Teddy? | Teddy's story | Next | Contact support | Instructions | Add location
    # Buttons | Instructions | Add location | are available only after entering secret code
    # Buttons | You got Teddy? | Teddy's story | Help | Contact Support | are activated irrespective of context,
    # Buttons | Instructions | Add location | are activated always in context 'code_correct',
    # other buttons ( Yes | No, thanks | Cancel | Next) - depend on context, if contexts==[] or irrelevant context - they
    # should return a response for a Fallback_Intent

    global CONTEXTS
    # A fix intended not to respond to every image uploaded (if several)
    if 'last_input_media' in CONTEXTS:
        CONTEXTS.remove('last_input_media')
        CONTEXTS.remove('media_input')

    bot.answer_callback_query(call.id, text="")

    # Get input data
    users_input = call.data
    chat_id = call.message.chat.id
    from_user = call.from_user

    # And pass it to the main handler function [main_hadler()]
    main_handler(users_input, chat_id, from_user, is_btn_click=True, geodata=None, media=False)


@bot.message_handler(content_types=['location'])
def location_handler(message):
    global CONTEXTS
    global NEWLOCATION
    # A fix intended not to respond to every image uploaded (if several)
    if 'last_input_media' in CONTEXTS:
        CONTEXTS.remove('last_input_media')
        CONTEXTS.remove('media_input')

    # Get input data
    users_input = 'User posted location'
    chat_id = message.chat.id
    from_user = message.from_user
    lat = message.location.latitude
    lng = message.location.longitude

    # And pass it to the main handler function [main_hadler()]
    main_handler(users_input, chat_id, from_user, is_btn_click=False, geodata={'lat': lat, 'lng': lng}, media=False)

@bot.message_handler(content_types=['photo'])
def photo_handler(message):
    '''
        The problem is that user may upload several photos, each one is proccessed separately and thus
        we get duplicate responses for every image (plus if updating contexts [delete 'media_input', append
        'if_comments'] after the 1st image then the 2nd and so on images trigger Fallback)
        Possible solution is to respond only to the 1st image and to save in contexts that the last input was image
        (plus not remove 'media_input' context)
        Then on the next input:
        if image - process it but don't respond,
        else - respond as usual, remove from contexts 'media_input' and the flag indicating that the last input was image
    '''
    global NEWLOCATION
    global CONTEXTS

    # Get input data
    chat_id = message.chat.id
    from_user = message.from_user

    # Get, check, save photos, add paths to NEWLOCATION['photos]
    if 'media_input' in CONTEXTS:
        file = bot.get_file(message.photo[-1].file_id)
        image_url = 'https://api.telegram.org/file/bot{0}/{1}'.format(TG_TOKEN, file.file_path)
        image_name = image_url.split("/")[-1]
        try:
            photo_filename = secure_filename(image_name)
            if tg_functions.valid_url_extension(photo_filename) and tg_functions.valid_url_mimetype(photo_filename):
                file_name_wo_extension = os.path.splitext(photo_filename)[0]
                if len(file_name_wo_extension) > 30:
                    file_name_wo_extension = file_name_wo_extension[:30]
                file_extension = os.path.splitext(photo_filename)[1]
                current_datetime = datetime.datetime.now().strftime("%d%m%y%H%M%S")
                path = PHOTO_DIR + file_name_wo_extension + '-' + current_datetime + file_extension
                # !!!
                path4db = 'uploads/' + file_name_wo_extension + '-' + current_datetime + file_extension

                r = requests.get(image_url, timeout=0.5)
                if r.status_code == 200:
                    with open(path, 'wb') as f:
                        f.write(r.content)
                NEWLOCATION['photos'].append(path4db)
                users_input = 'User posted a photo'

                # Contexts - indicate that last input was an image
                if 'last_input_media' not in CONTEXTS:
                    CONTEXTS.append('last_input_media')
                    users_input = 'User uploaded an image'
                    main_handler(users_input, chat_id, from_user, is_btn_click=False, geodata=None, media=True)
        except Exception as e:
            print('photo_handler() exception: {}'.format(e))
            users_input = 'File has invalid image extension or invalid image format'
            main_handler(users_input, chat_id, from_user, is_btn_click=False, geodata=None, media=False)
    else:
        if 'last_input_media' not in CONTEXTS:
            CONTEXTS.append('last_input_media')
            users_input = 'Nice image ;)'
            print('Really true!')
            print('CONTEXTS: {}'.format(CONTEXTS))
            main_handler(users_input, chat_id, from_user, is_btn_click=False, geodata=None, media=True)

################################### 'Custom' handlers END ##################################

####################################### Functions START ####################################


def dialogflow(query, chat_id, lang_code='en'):
    '''
        Function to communicate with Dialogflow for NLP
    '''
    URL = 'https://api.dialogflow.com/v1/query?v=20170712'
    HEADERS = {'Authorization': 'Bearer ' + DF_TOKEN, 'content-type': 'application/json'}
    payload = {'query': query, 'sessionId': chat_id, 'lang': lang_code}
    r = requests.post(URL, data=json.dumps(payload), headers=HEADERS).json()
    intent = r.get('result').get('metadata').get('intentName')
    speech = r.get('result').get('fulfillment').get('speech')
    status = r.get('status').get('code')
    output = {
        'status': status,
        'intent': intent,
        'speech': speech
    }
    return output

def main_handler(users_input, chat_id, from_user, is_btn_click=False, geodata=None, media=False):
    '''
        Main handler. Function gets input from user (typed text OR callback_data from button clicks), 'feeds' it
        to Dialogflow for NLP, receives intent and speech, and then depending on intent and context responds to user
        users_input - typed text or callback_data from button
        chat_id - chat ID (call.message.chat.id or message.chat.id)
        from_user - block of data about user (1st name, id etc)
        is_btn_click - whether it's callback_data from button (True) or manual text input (False, default)
        geodata - dictionary with latitude/longitude or None (default)
    '''
    global CONTEXTS
    global NEWLOCATION

    if geodata:
        speech = 'Nice place ;)'
        intent = 'location_received'
    elif media:
        speech = 'Nice image ;)'
        intent = 'media_received'
    else:
        dialoflows_response = dialogflow(users_input, chat_id)
        speech = dialoflows_response['speech']
        intent = dialoflows_response['intent']

    # Block 1. Traveler's story
    # Block 1-1. Reply to typing/clocking_buttons 'Yes'/'No' displayed after the intro block asking
    # if user want's to know more about T. journey
    # On exit of block if user enters 'Yes' - context 'journey_next_info', if 'No' or he/she clicks buttons of
    # previous blocks - contexts[] is cleared
    if 'if_journey_info_needed' in CONTEXTS:
        if intent == 'smalltalk.confirmation.no':
            time.sleep(SHORT_TIMEOUT)
            if 'if_journey_info_needed' in CONTEXTS:
                CONTEXTS.remove('if_journey_info_needed')
            bot.send_message(chat_id, 'Ok. Than we can just talk ;)\nJust in case here\'s my menu',
                             reply_markup=chatbot_markup.intro_menu)
        elif intent == 'smalltalk.confirmation.yes':
            journey_intro(chat_id)
            if 'if_journey_info_needed' in CONTEXTS:
                CONTEXTS.remove('if_journey_info_needed')
            CONTEXTS.append('journey_next_info')
        # If user is clicking buttons under previous blocks (for eg., buttons 'FAQ', <Traveler>'s story, You got traveler)
        # call classifier() with cleaned contexts
        else:
            # Buttons | You got Teddy? | Teddy's story | Help | are activated irrespective of context
            if not always_triggered(chat_id, intent, speech):
                # All other text inputs/button clicks
                default_fallback(chat_id, intent, speech)

    # Block 1-2. Reply to entering/clicking buttons 'Next/Help' after block#1 showing overall map of traveler's journey;
    # on entry - context 'journey_next_info',
    # on exit of block if user clicks/types
    # 1) 'Next' and
    # a) if only 1 place was visited - contexts[] is cleared
    # b) if several places were visited - 2 contexts are added:
    # 'journey_summary_presented' and {'location_shown': None, 'total_locations': total_locations}
    # 2) 'Help' or clicks buttons of previous blocks - contexts[] is cleared
    elif 'journey_next_info' in CONTEXTS:
        if intent == 'next_info':
            total_locations = journey_begins(chat_id, OURTRAVELLER)
            time.sleep(SHORT_TIMEOUT)
            # If there's only 1 location, show it and present basic menu ("Teddy's story/Help/You got Teddy?")
            if total_locations == 1:
                the_1st_place(chat_id, OURTRAVELLER, False)
                bot.send_message(chat_id,
                                 'And that\'s all my journey so far ;)\n\nWhat would you like to do next? We can just talk or use this menu:',
                                 reply_markup=chatbot_markup.intro_menu)
                if 'journey_next_info' in CONTEXTS:
                    CONTEXTS.remove('journey_next_info')
            # If there are >1 visited places, ask user if he wants to see them ("Yes/No/Help")
            else:
                bot.send_message(chat_id, 'Would you like to see all places that I have been to?',
                                 reply_markup=chatbot_markup.yes_no_help_menu)
                if 'journey_next_info' in CONTEXTS:
                    CONTEXTS.remove('journey_next_info')
                CONTEXTS.append('journey_summary_presented')
                CONTEXTS.append({'location_shown': None, 'total_locations': total_locations})
        elif intent == 'show_faq':
            if 'journey_next_info' in CONTEXTS:
                CONTEXTS.remove('journey_next_info')
            get_help(chat_id)
        # If user is clicking buttons under previous blocks - call classifier() with cleaned contexts
        else:
            # Buttons | You got Teddy? | Teddy's story are activated irrespective of context
            if not always_triggered(chat_id, intent, speech):
                # All other text inputs/button clicks
                default_fallback(chat_id, intent, speech)

    # Block 1-3. Reply to entering/clicking buttons 'Yes/No,thanks/Help' displayed after the prevoius block with journey summary;
    # on entry - 2 contexts 'journey_summary_presented' and {'location_shown': None, 'total_locations': total_locations},
    # on exit:
    # 1) if user types/clicks 'Yes' - 2 contexts 'locations_iteration' and {'location_shown': 0, 'total_locations': total_locations}
    # 2) if user types/clicks 'No/Help' or clicks buttons of previous blocks - contexts[] is cleared
    elif 'journey_summary_presented' in CONTEXTS:
        if intent == 'smalltalk.confirmation.yes':  # "Yes" button is available if >1 places were visited
            the_1st_place(chat_id, OURTRAVELLER, True)
            if 'journey_summary_presented' in CONTEXTS:
                CONTEXTS.remove('journey_summary_presented')
            if 'locations_iteration' not in CONTEXTS:
                CONTEXTS.append('locations_iteration')
            for context in CONTEXTS:
                if 'location_shown' in context:
                    context['location_shown'] = 0
        elif intent == 'smalltalk.confirmation.no':
            time.sleep(SHORT_TIMEOUT)
            if 'journey_summary_presented' in CONTEXTS:
                CONTEXTS.remove('journey_summary_presented')
            bot.send_message(chat_id, 'Ok. Than we can just talk ;)\nJust in case here\'s my menu',
                             reply_markup=chatbot_markup.intro_menu)
        elif intent == 'show_faq':
            if 'journey_summary_presented' in CONTEXTS:
                CONTEXTS.remove('journey_summary_presented')
            get_help(chat_id)
        # If user is clicking buttons under previous blocks - call classifier() with cleaned contexts
        else:
            # Buttons | You got Teddy? | Teddy's story are activated irrespective of context
            if not always_triggered(chat_id, intent, speech):
                # All other text inputs/button clicks
                default_fallback(chat_id, intent, speech)

    # Block 1-4. Reply to entering/clicking buttons 'Next/Help' after block#3 showing the 1st place among several visited;
    # is executed in cycle
    # on entry - 2 contexts: 'locations_iteration' and {'location_shown': X, 'total_locations': Y}
    # (where X = the serial number of place visited, for eg. 0 - the 1st place, 2 - the 3rd place),
    # on exit:
    # 1) if user types/clicks 'Yes' and
    # a) if the last place visited is shown - contexts[] is cleared
    # b) if places to show remain - 2 contexts: 'locations_iteration' and {'location_shown': X+1, 'total_locations': Y}
    # 2) types/ckicks button 'Help' or buttons of previous blocks - contexts[] is cleared
    elif 'locations_iteration' in CONTEXTS:
        if intent == 'next_info':
            location_shown = 0
            total_locations = 1
            for context in CONTEXTS:
                if 'location_shown' in context:
                    location_shown = context['location_shown']
                    total_locations = context['total_locations']
            if total_locations - (location_shown + 1) == 1:
                if 'locations_iteration' in CONTEXTS:
                    CONTEXTS.remove('locations_iteration')
                every_place(chat_id, OURTRAVELLER, location_shown + 1, False)
                bot.send_message(chat_id,
                                 'And that\'s all my journey so far ;)\n\nWhat would you like to do next? We can just talk or use this menu:',
                                 reply_markup=chatbot_markup.intro_menu)
            elif total_locations - (location_shown + 1) > 1:
                every_place(chat_id, OURTRAVELLER, location_shown + 1, True)
                for context in CONTEXTS:
                    if 'location_shown' in context:
                        context['location_shown'] += 1
        elif intent == 'show_faq':
            if 'locations_iteration' in CONTEXTS:
                CONTEXTS.remove('locations_iteration')
            get_help(chat_id)
        # If user is clicking buttons under previous blocks - call classifier() with cleaned contexts
        else:
            # Buttons | You got Teddy? | Teddy's story are activated irrespective of context
            if not always_triggered(chat_id, intent, speech):
                # All other text inputs/button clicks
                default_fallback(chat_id, intent, speech)

    # Block 2. If you got a fellow traveler
    # Block 2-1. User clicked button/typed 'You got Teddy?' and was prompted to enter the secret code
    elif 'enters_code' in CONTEXTS:
        # If user enters 'Cancel' or smth similar after entering invalid secret_code - update contexts
        if intent == 'smalltalk.confirmation.cancel':
            if 'enters_code' in CONTEXTS:
                CONTEXTS.remove('enters_code')
            bot.send_message(chat_id, 'Ok. What would you like to do next?',
                         reply_markup=chatbot_markup.intro_menu)
        elif intent == 'contact_support':
            if 'enters_code' in CONTEXTS:
                CONTEXTS.remove('enters_code')
            CONTEXTS.append('contact_support')
            bot.send_message(chat_id, 'If you\'ve got some problems, have any questions, suggestions, remarks, proposals etc - please enter them below.\nYou can also write directly to my email <b>iurii.dziuban@gmail.com</b>.',
                             parse_mode='html', reply_markup=chatbot_markup.intro_menu)
        # If user enters whatever else, not == intent 'smalltalk.confirmation.cancel'
        else:
            if not is_btn_click:
                secret_code_entered = users_input
                if secret_code_validation(secret_code_entered):
                    if 'enters_code' in CONTEXTS:
                        CONTEXTS.remove('enters_code')
                    CONTEXTS.append('code_correct')
                    bot.send_message(chat_id, 'Code correct, thanks! Sorry for formalities')
                    bot.send_message(chat_id,
                                     'As I might have said, my goal is to see the world.'
                                     '\n\n And as your fellow traveler I will kindly ask you for 2 things:'
                                     '\n- Please show me some nice places of your city/country or please take me with you if you are traveling somewhere. '
                                     'Please document where I have been using the button "<b>Add location</b>".'
                                     '\n - After some time please pass me to somebody else ;)'
                                     '\n\n For more detailed instructions - please click "<b>Instructions</b>"'
                                     '\n\nIf you\'ve got some problems, you can also write to my author (button "<b>Contact support</b>")',
                                     parse_mode='html', reply_markup=chatbot_markup.you_got_teddy_menu)
                else:
                    bot.send_message(chat_id, 'Incorrect secret code. Please try again',
                                     reply_markup=chatbot_markup.cancel_help_contacts_menu)
            else:
                # Buttons | You got Teddy? | Teddy's story | Help | are activated irrespective of context
                if not always_triggered(chat_id, intent, speech):
                    # All other text inputs/button clicks
                    default_fallback(chat_id, intent, speech)

    # Block 2-2. User entered correct password and now can get 'priviledged' instructions, add location or contact support
    # Context 'code_correct' is being cleared after adding a new location, clicking 'Contact support' or if user enters
    # commands outside of of block that is displayed after entering secret code
    elif 'code_correct' in CONTEXTS:
        if intent == 'contact_support':
            CONTEXTS.clear()
            CONTEXTS.append('code_correct')
            CONTEXTS.append('contact_support')
            bot.send_message(chat_id, 'If you\'ve got some problems, have any questions, suggestions, remarks, proposals etc - please enter them below.\nYou can also write directly to my email <b>iurii.dziuban@gmail.com</b>.',
                             parse_mode='html', reply_markup=chatbot_markup.intro_menu)
        elif intent == 'show_instructions':
            CONTEXTS.clear()
            CONTEXTS.append('code_correct')
            bot.send_message(chat_id, 'Here are our detailed instructions for those who got {}'.format(OURTRAVELLER),
                             parse_mode='html', reply_markup=chatbot_markup.you_got_teddy_menu)
        elif intent == 'add_location':
            bot.send_message(chat_id, 'First please tell <i>where</i> {} <i>is now</i> (you may use the button \"<b>Share your location</b>\" below) \n\nor \n\n<i>where he was</i> photographed (to enter address which differs from your current location please <b>attach >> Location</b> and drag the map to desired place)'.format(OURTRAVELLER),
                             parse_mode='html', reply_markup=chatbot_markup.share_location)
            if 'location_input' not in CONTEXTS:
                CONTEXTS.append('location_input')
        else:
            # Block 2-3. User enters location ('location_input' in contexts)
            # It can be either his/her current location shared using Telegram's location sharing function or a plain text input
            # from text_handler() which should be processed using Google Maps Geocoding API
            if 'location_input' in CONTEXTS:
                # And user shared his/her location
                if intent == 'location_received':  # sharing or current location
                    # Reverse geocode lat/lng to geodata
                    # Also as this is the 1st data for new locations, fill the fields 'author', 'channel' and 'user_id_on_channel'
                    NEWLOCATION['author'] = from_user.first_name
                    NEWLOCATION['user_id_on_channel'] = from_user.id
                    NEWLOCATION['channel'] = 'Telegram'
                    NEWLOCATION['longitude'] = geodata['lng']
                    NEWLOCATION['latitude'] = geodata['lat']
                    # Erase the remaining fields of NEWLOCATION in case user restarts
                    NEWLOCATION['formatted_address'] = None
                    NEWLOCATION['locality'] = None
                    NEWLOCATION['administrative_area_level_1'] = None
                    NEWLOCATION['country'] = None
                    NEWLOCATION['place_id'] = None
                    NEWLOCATION['comment'] = None
                    NEWLOCATION['photos'] = []

                    gmaps_geocoder(geodata['lat'], geodata['lng'])
                    CONTEXTS.remove('location_input')
                    # Ready for the next step - adding photos
                    CONTEXTS.append('media_input')
                    bot.send_message(chat_id,
                                     'Thanks! Now could you please upload some photos with {0} from this place?\nSelfies with {0} are also welcome ;)'.format(
                                         OURTRAVELLER), parse_mode='html',
                                     reply_markup=chatbot_markup.next_reset_instructions_menu)

                # User cancels location entry - leave 'code_correct' context, remove 'location_input'
                elif intent == 'smalltalk.confirmation.cancel':
                    CONTEXTS.remove('location_input')
                    bot.send_message(chat_id,
                                     'Ok. What would you like to do next?',
                                     parse_mode='html', reply_markup=chatbot_markup.you_got_teddy_menu)

                # User wants detailed instructions - contexts unchanged
                elif intent == 'show_instructions':
                    bot.send_message(chat_id,
                                     'Here are our detailed instructions for those who got {}'.format(OURTRAVELLER),
                                     parse_mode='html', reply_markup=chatbot_markup.you_got_teddy_menu)

                # User should be entering location but he/she types smth or clicks other buttons besides 'Cancel' or
                # 'Instructions'
                else:
                    bot.send_message(chat_id,
                                     'That doesn\'t look like a valid location. Please try once again',
                                     parse_mode='html', reply_markup=chatbot_markup.cancel_or_instructions_menu)
                    if not always_triggered(chat_id, intent, speech):
                        # All other text inputs/button clicks
                        default_fallback(chat_id, intent, speech)

            # Block 2-4. User should be uploading a/some photo/-s.
            elif 'media_input' in CONTEXTS:
                # User did upload some photos - thank him/her and ask for a comment
                if intent == 'media_received':
                    # If user uploaded several images - respond only to the 1st one
                    if 'last_media_input' not in CONTEXTS:
                        if 'any_comments' not in CONTEXTS:
                            CONTEXTS.append('any_comments')
                        bot.send_message(chat_id,
                                         'Thank you!\n'
                                         'Any comments (how did you get {0}, what did you feel, any messages for future {0}\'s fellow travelers)?'.format(OURTRAVELLER),
                                         parse_mode='html', reply_markup=chatbot_markup.next_reset_instructions_menu)

                # User refused to upload photos, clicked 'Next' - ask him/her for a comment
                elif intent == 'next_info':
                    CONTEXTS.clear()
                    CONTEXTS.append('code_correct')
                    CONTEXTS.append('any_comments')
                    bot.send_message(chat_id,
                                     'Ok\n'
                                     'Any comments (how did you get {0}, what did you feel, any messages for future {0}\'s fellow travelers)?'.format(
                                         OURTRAVELLER),
                                     parse_mode='html', reply_markup=chatbot_markup.next_reset_instructions_menu)

                else:
                    # User should be uploading photos but he/she didn't and also didn't click 'Cancel' but
                    # enters/clicks something else
                    # Buttons | You got Teddy? | Teddy's story | Help | etc are activated irrespective of context
                    if not always_triggered(chat_id, intent, speech):
                        # All other text inputs/button clicks
                        default_fallback(chat_id, intent, speech)

            # Block 2-5. User was prompted to leave a comment and entered some text
            elif 'any_comments' in CONTEXTS \
                    and 'last_media_input' not in CONTEXTS:
                if not is_btn_click:
                    # Update contexts - leave only 'code_correct' and 'any_comments'
                    CONTEXTS.clear()
                    CONTEXTS.append('code_correct')
                    CONTEXTS.append('any_comments')

                    # Show user what he/she has entered as a comment
                    bot.send_message(chat_id,
                                     'Ok. So we\'ll treat the following as your comment:\n<i>{}</i>'.format(users_input),
                                     parse_mode='html')
                    # Save user's comment to NEWLOCATION
                    NEWLOCATION['comment'] = users_input

                    # Update contexts - remove 'any_comments', add 'ready_for_submit'
                    CONTEXTS.remove('any_comments')
                    CONTEXTS.append('ready_for_submit')

                    # Resume up user's input (location, photos, comment) and ask to confirm or reset
                    time.sleep(SHORT_TIMEOUT)
                    bot.send_message(chat_id,
                                     'In total your input will look like this:', parse_mode='html')
                    if new_location_summary(chat_id, from_user):
                        bot.send_message(chat_id,
                                         'Is that Ok? If yes, please click \"<b>Submit</b>\".\nOtherwise click \"<b>Reset</b>\" to start afresh',
                                         parse_mode='html', reply_markup=chatbot_markup.submit_reset_menu)
                    else:
                        bot.send_message(chat_id,
                                         'Hmm.. Some error occured. Could you please try again?',
                                         parse_mode='html', reply_markup=chatbot_markup.you_got_teddy_menu)
                else:
                    # User doesn't want to give a comment
                    if intent == 'next_info':
                        NEWLOCATION['comment'] = ''
                        CONTEXTS.remove('any_comments')
                        CONTEXTS.append('ready_for_submit')
                        # Resume up user's input (location, photos, comment) and ask to confirm or reset
                        time.sleep(SHORT_TIMEOUT)
                        bot.send_message(chat_id,
                                         'In total your input will look like this:', parse_mode='html')
                        if new_location_summary(chat_id, from_user):
                            bot.send_message(chat_id,
                                         'Is that Ok? If yes, please click \"<b>Submit</b>\".\nOtherwise click \"<b>Reset</b>\" to start afresh',
                                         parse_mode='html', reply_markup=chatbot_markup.submit_reset_menu)
                        else:
                            bot.send_message(chat_id,
                                         'Hmm.. Some error occured. Could you please try again?',
                                         parse_mode='html', reply_markup=chatbot_markup.you_got_teddy_menu)
                    elif intent == 'reset':
                        CONTEXTS.clear()
                        CONTEXTS.append('code_correct')
                        bot.send_message(chat_id,
                                         'Ok, let\'s try once again',
                                         parse_mode='html', reply_markup=chatbot_markup.you_got_teddy_menu)
                    else:
                        # Buttons | You got Teddy? | Teddy's story | Help | are activated irrespective of context
                        if not always_triggered(chat_id, intent, speech):
                            # All other text inputs/button clicks
                            default_fallback(chat_id, intent, speech)

            # Block 2-6. Submitting new location - user clicked 'Submit'
            elif 'ready_for_submit' in CONTEXTS:
                if intent == 'submit':
                    if submit_new_location(OURTRAVELLER):
                        # Clear all contexts
                        CONTEXTS.clear()

                        # Call function to generate new secret code
                        # new_secret_code = code_regenerate(traveller)

                        bot.send_message(chat_id,
                                         'New location added!\n\n'
                                         'Secret code for adding the next location: <code>XXX</code>\n\n'
                                         'Please save it somewhere or don\'t delete this message.\n'
                                         'If you are going to pass {} to somebody please write this code similar to how you received it'.format(OURTRAVELLER),
                                         parse_mode='html', reply_markup=chatbot_markup.intro_menu)
                    else:
                        # Clear all contexts
                        CONTEXTS.clear()
                        bot.send_message(chat_id, 'Hmm.. failed to save new location.\n'
                                                  'Could you please try once again?',
                                         parse_mode='html', reply_markup=chatbot_markup.intro_menu)
                elif intent == 'reset':
                    CONTEXTS.clear()
                    CONTEXTS.append('code_correct')
                    bot.send_message(chat_id,
                                     'Ok, let\'s try once again',
                                     parse_mode='html', reply_markup=chatbot_markup.you_got_teddy_menu)
                else:
                    # Buttons | You got Teddy? | Teddy's story | Help | are activated irrespective of context
                    if not always_triggered(chat_id, intent, speech):
                        # All other text inputs/button clicks
                        default_fallback(chat_id, intent, speech)

            else:
                # Buttons | You got Teddy? | Teddy's story | Help | are activated irrespective of context
                if not always_triggered(chat_id, intent, speech):
                    # All other text inputs/button clicks
                    default_fallback(chat_id, intent, speech)

    # General endpoint - if user typed/clicked something and contexts[] is empty
    else:
        # Buttons | You got Teddy? | Teddy's story | Help | are activated irrespective of context
        if not always_triggered(chat_id, intent, speech):
            # All other text inputs/button clicks
            default_fallback(chat_id, intent, speech)

    # Console logging
    print('')
    if is_btn_click:
        input_type = 'button click'
    elif media:
        input_type = 'media upload'
    elif geodata:
        input_type = 'location input'
    else:
        input_type = 'entered manually'
    print('User\'s input: {} ({})'.format(users_input, input_type))
    print('Intent: {}, speech: {}'.format(intent, speech))
    print('Contexts: {}'.format(CONTEXTS))

def always_triggered(chat_id, intent, speech):
    '''
        Buttons | You got Teddy? | Teddy's story | Help | are activated always, irrespective of context
        Buttons | Instructions | Add location | are activated always in context 'code_correct'
    '''
    global CONTEXTS

    # User typed 'Help' or similar
    if intent == 'show_faq':
        get_help(chat_id)
        return True

    # User typed 'Teddy's story' or similar
    elif intent == 'tell_your_story':
        bot.send_photo(chat_id, 'https://iuriid.github.io/img/ft-1.jpg',
                       caption='My name is <strong>{}</strong>. I\'m a traveler.\nMy dream is to see the world'.format(
                           OURTRAVELLER), parse_mode='html')
        time.sleep(SHORT_TIMEOUT)
        bot.send_message(chat_id, 'Do you want to know more about my journey?',
                         reply_markup=chatbot_markup.yes_no_gotteddy_menu)
        if 'if_journey_info_needed' not in CONTEXTS:
            CONTEXTS.append('if_journey_info_needed')
        return True

    # User typed "You got Teddy" or similar
    elif intent == 'you_got_fellowtraveler':
        if 'code_correct' not in CONTEXTS:
            bot.send_message(chat_id,
                             'Oh, that\'s a tiny adventure and some responsibility ;)\nTo proceed please <b>enter the secret code</b> from the toy',
                             parse_mode='html')
            # Image with an example of secret code
            bot.send_photo(chat_id, 'https://iuriid.github.io/img/ft-3.jpg',
                           reply_markup=chatbot_markup.cancel_help_contacts_menu)
            CONTEXTS.clear()
            CONTEXTS.append('enters_code')
        else:
            bot.send_message(chat_id,
                             'Ok. What would you like to do next?',
                             parse_mode='html', reply_markup=chatbot_markup.you_got_teddy_menu)
        return True

    # User clicks/types "Contact support"
    elif intent == 'contact_support':
        if 'contact_support' not in CONTEXTS:
            CONTEXTS.append('contact_support')
            bot.send_message(chat_id, 'If you\'ve got some problems, have any questions, suggestions, remarks, proposals etc - please enter them below.\nYou can also write directly to my email <b>iurii.dziuban@gmail.com</b>.',
                         parse_mode='html', reply_markup=chatbot_markup.intro_menu)
        return True

    # Buttons | Instructions | Add location | are activated always in context 'code_correct'
    if 'code_correct' in CONTEXTS:
        if intent == 'show_instructions':
            bot.send_message(chat_id, 'Here are our detailed instructions for those who got {}'.format(OURTRAVELLER),
                             parse_mode='html', reply_markup=chatbot_markup.you_got_teddy_menu)
            return True

        elif intent == 'add_location':
            bot.send_message(chat_id, 'First please tell where {} is now or in what place he was photographed\nPlease type approximate address or share your location'.format(OURTRAVELLER),
                             parse_mode='html', reply_markup=chatbot_markup.share_location)
            if 'location_input' not in CONTEXTS:
                CONTEXTS.append('location_input')
            return True

    else:
        return False


def default_fallback(chat_id, intent, speech):
    '''
        Response for all inputs (manual entry or button clicks) which are irrelevant to current context
    '''
    global CONTEXTS

    code_correct_flag, location_input_flag, last_input_media_flag = False, False, False
    if 'code_correct' in CONTEXTS:
        code_correct_flag = True
    if 'location_input' in CONTEXTS:
        location_input_flag = True
    if 'last_input_media' in CONTEXTS:
        last_input_media_flag = True
    CONTEXTS.clear()
    if code_correct_flag:
        CONTEXTS.append('code_correct')
    if location_input_flag:
        CONTEXTS.append('location_input')
    if last_input_media_flag:
        CONTEXTS.append('last_input_media')

    bot.send_message(chat_id, speech)
    time.sleep(SHORT_TIMEOUT)
    bot.send_message(chat_id, 'What would you like to do next?', reply_markup=chatbot_markup.intro_menu)

def travelers_story_intro(chat_id):
    '''
        Traveler presents him/herself, his/her goal and asks if user would like to know more about traveler's journey
    '''
    # Traveler's photo
    bot.send_photo(chat_id, 'https://iuriid.github.io/img/ft-1.jpg',
                   caption='My name is <strong>{}</strong>. I\'m a traveler.\nMy dream is to see the world'.format(
                       OURTRAVELLER), parse_mode='html')
    time.sleep(SHORT_TIMEOUT)
    bot.send_message(chat_id, 'Do you want to know more about my journey?',
                     reply_markup=chatbot_markup.yes_no_gotteddy_menu)

def journey_intro(chat_id):
    '''
        Block 1.
        Displays short general 'intro' information about traveller's origin (for eg., 'I came from Cherkasy city,
        Ukraine, from a family with 3 nice small kids'), presents a map with all visited locations with a link to
        web-map and then user has a choice to click 'Next', 'Help' or just to talk about something
    '''
    time.sleep(SHORT_TIMEOUT)
    bot.send_message(chat_id, 'Ok, here is my story')
    time.sleep(MEDIUM_TIMEOUT)
    bot.send_message(chat_id,
                     'I came from <a href="{}">Cherkasy</a> city, Ukraine, from a family with 3 nice small kids'.format(
                         'https://www.google.com/maps/place/Черкассы,+Черкасская+область,+18000/@50.5012899,25.9683426,6z'),
                     parse_mode='html', disable_web_page_preview=True)
    bot.send_photo(chat_id, 'https://iuriid.github.io/img/ft-4.jpg')
    time.sleep(LONG_TIMEOUT)
    bot.send_message(chat_id,
                     'So far the map of my journey looks as follows:',
                     parse_mode='html')
    bot.send_chat_action(chat_id, action='upload_photo')
    bot.send_photo(chat_id, 'https://iuriid.github.io/img/ft-3.jpg',
                         caption='<a href="{}">Open map in browser</a>'.format(
                             'https://fellowtraveler.club/#journey_map'), parse_mode='html',
                         reply_markup=chatbot_markup.next_or_help_menu)


def journey_begins(chat_id, traveller):
    '''
        Block 2.
        Retrieves journey summary for a given traveller from DB and presents it (depending on quantity of places
        visited, the only one can be also shown or user may be asked if he want's to see the places)
    '''
    client = MongoClient()
    db = client.TeddyGo

    # Message: I've checked in ... places in ... country[ies] (country1 [, country2 etc]) and have been traveling for ... days so far
    tg_functions.summarize_journey(traveller)
    traveller_summary = db.travellers.find_one({'name': traveller})
    #print('traveller_summary: {}'.format(traveller_summary))
    total_locations = traveller_summary['total_locations']
    total_countries = traveller_summary['total_countries']
    countries_visited = traveller_summary['countries_visited']
    countries = ', '.join(countries_visited)
    journey_duration = tg_functions.time_passed(traveller)
    if total_countries == 1:
        countries_form = 'country'
    else:
        countries_form = 'countries'
    if journey_duration == 1:
        day_or_days = 'day'
    else:
        day_or_days = 'days'
    speech = 'So far I\'ve checked in <strong>{}</strong> places located in <strong>{}</strong> {} ({}) and have been traveling for <strong>{}</strong> {}.\n\nI covered about ... km it total and currently I\'m nearly .. km from home'.format(
        total_locations, total_countries, countries_form, countries, journey_duration, day_or_days)
    bot.send_message(chat_id, speech, parse_mode='html')
    return total_locations


def the_1st_place(chat_id, traveller, if_to_continue):
    '''
        Block 3 and also inside block 2
        Shows the place our traveller came from. Is used either directly after journey summary (if only 1 or 2 places
        were visited so far) or as the first place in cycle showing all places visited
    '''
    print()
    #print('the_1st_place - if_to_continue: {}'.format(if_to_continue))
    client = MongoClient()
    db = client.TeddyGo

    # Message: I started my journey in ... on ...
    the_1st_location = db[traveller].find()[0]
    formatted_address = the_1st_location['formatted_address']
    lat = the_1st_location['latitude']
    long = the_1st_location['longitude']
    start_date = '{}'.format(the_1st_location['_id'].generation_time.date())
    time_passed = tg_functions.time_passed(traveller)
    if time_passed == 0:
        day_or_days = 'today'
    elif time_passed == 1:
        day_or_days = '1 day'
    else:
        day_or_days = '{} days'.format(time_passed)
    message1 = '<strong>Place #1</strong>\nI started my journey on {} ({}) from \n<i>{}</i>'.format(start_date, day_or_days, formatted_address)
    bot.send_message(chat_id, message1, parse_mode='html')
    bot.send_location(chat_id, latitude=lat, longitude=long)
    photos = the_1st_location['photos']
    if len(photos) > 0:
        for photo in photos:
            print(photo)
            bot.send_photo(chat_id, 'https://iuriid.github.io/img/ft-3.jpg')
            bot.send_photo(chat_id, 'https://iuriid.github.io/img/ft-1.jpg')
    author = the_1st_location['author']
    comment = the_1st_location['comment']
    message2 = 'That was the 1st place'
    if comment != '':
        if author == 'Anonymous':
            author = '(who decided to remain anonymous)'
        else:
            author = '<b>{}</b>'.format(author)
        message2 = 'My new friend {} wrote:\n<i>{}</i>'.format(author, comment)
    else:
        if author != 'Anonymous':
            message2 = 'I got acquainted with a new friend - {} :)'.format(author)
    if if_to_continue:
        bot.send_message(chat_id, message2, parse_mode='html', reply_markup=chatbot_markup.next_or_help_menu)
        #print('Here')
    else:
        bot.send_message(chat_id, message2, parse_mode='html')
        #print('There')


def every_place(chat_id, traveller, location_to_show, if_to_continue):
    '''
        Block 4
        Shows the 2nd and further visited places
    '''
    client = MongoClient()
    db = client.TeddyGo

    # Message: I started my journey in ... on ...
    location = db[traveller].find()[location_to_show]

    formatted_address = location['formatted_address']
    lat = location['latitude']
    long = location['longitude']
    location_date = '{}'.format(location['_id'].generation_time.date())
    location_date_service = location['_id'].generation_time
    time_passed = time_from_location(traveller, location_date_service)
    if time_passed == 0:
        day_or_days = 'today'
    elif time_passed == 1:
        day_or_days = '1 day ago'
    else:
        day_or_days = '{} days ago'.format(time_passed)
    message1 = '<strong>Place #{}</strong>\nOn {} ({}) I was in \n<i>{}</i>'.format(location_to_show + 1,
                                                                                             location_date, day_or_days,
                                                                                             formatted_address)
    bot.send_message(chat_id, message1, parse_mode='html')
    bot.send_location(chat_id, latitude=lat, longitude=long)
    photos = location['photos']
    if len(photos) > 0:
        for photo in photos:
            print(photo)
            bot.send_photo(chat_id, 'https://iuriid.github.io/img/ft-3.jpg')
            bot.send_photo(chat_id, 'https://iuriid.github.io/img/ft-1.jpg')
    author = location['author']
    comment = location['comment']
    message2 = 'That was the place #{}'.format(location_to_show + 1)
    if comment != '':
        if author == 'Anonymous':
            author = '(who decided to remain anonymous)'
        else:
            author = '<b>{}</b>'.format(author)
        message2 = 'My new friend {} wrote:\n<i>{}</i>'.format(author, comment)
    else:
        if author != 'Anonymous':
            message2 = 'I got acquainted with a new friend - {} :)'.format(author)
    if if_to_continue:
        bot.send_message(chat_id, message2, parse_mode='html', reply_markup=chatbot_markup.next_or_help_menu)
    else:
        bot.send_message(chat_id, message2, parse_mode='html')


def get_help(chat_id):
    '''
        Displays FAQ/help
    '''
    global CONTEXTS

    CONTEXTS.clear()
    bot.send_message(chat_id, 'Here\'s our FAQ')
    bot.send_message(chat_id, 'What would you like to do next?',
                     reply_markup=chatbot_markup.intro_menu)


def secret_code_validation(secret_code_entered):
    '''
        Validates the secret code entered by user against the one in DB
        If code valid - updates contexts (remove 'enters_code', append 'code_correct')
        If code invalid - suggests to enter it again + inline button 'Cancel' (to remove context 'enters_code')
    '''
    client = MongoClient()
    db = client.TeddyGo
    collection_travellers = db.travellers
    teddys_sc_should_be = collection_travellers.find_one({"name": OURTRAVELLER})['secret_code']
    if not sha256_crypt.verify(secret_code_entered, teddys_sc_should_be):
        return False
    else:
        return True

def gmaps_geocoder(lat, lng):
    '''
    Google Maps - reverse geocoding (https://developers.google.com/maps/documentation/geocoding/start#reverse)
    Getting geodata (namely 'formatted_address', 'locality', 'administrative_area_level_1', 'country' and 'place_id')
    for coordinates received after location sharing in Telegram
    '''
    global NEWLOCATION

    URL = 'https://maps.googleapis.com/maps/api/geocode/json?latlng={},{}&key={}'.format(lat, lng, GOOGLE_MAPS_API_KEY)
    try:
        r = requests.get(URL).json().get('results')

        if r[0]:
            formatted_address = r[0].get('formatted_address')
            address_components = r[0].get('address_components')
            locality, administrative_area_level_1, country, place_id = None, None, None, None
            for address_component in address_components:
                types = address_component.get('types')
                short_name = address_component.get('short_name')
                # print("type: {}, short name: {}".format(types, short_name))
                if 'locality' in types:
                    locality = short_name
                elif 'administrative_area_level_1' in types:
                    administrative_area_level_1 = short_name
                elif 'country' in types:
                    country = short_name
            place_id = r[0].get('place_id')

        NEWLOCATION['formatted_address'] = formatted_address
        NEWLOCATION['locality'] = locality
        NEWLOCATION['administrative_area_level_1'] = administrative_area_level_1
        NEWLOCATION['country'] = country
        NEWLOCATION['place_id'] = place_id

        return True
    except Exception as e:
        print('gmaps_geocoder() exception: {}'.format(e))
        return False

def submit_new_location(traveller):
    '''
        Saves new location (NEWLOCATION) to DB
    '''
    global NEWLOCATION
    try:
        # Logging
        print('')
        print('Saving location to DB...')
        print('NEWLOCATION: {}'.format(NEWLOCATION))

        client = MongoClient()
        db = client.TeddyGo
        collection_teddy = db[traveller]
        NEWLOCATION.pop('_id', None)
        collection_teddy.insert_one(NEWLOCATION)
        return True
    except Exception as e:
        print('submit_new_location() exception: {}'.format(e))
        return False

def time_from_location(traveller, from_date):
    '''
        Function calculates time elapsed from the date when traveler was in specific location (from_date) to now
    '''
    current_datetime = datetime.datetime.now(timezone.utc)
    difference = (current_datetime - from_date).days
    return difference

def new_location_summary(chat_id, from_user):
    '''
        Functions sums up data on new location (held in NEWLOCATION variable) before saving the new location to DB
    '''
    try:
        location_date = datetime.datetime.now().strftime('%Y-%m-%d')
        message1 = 'On {} {} was in \n<i>{}</i>'.format(location_date, OURTRAVELLER, NEWLOCATION['formatted_address'])
        bot.send_message(chat_id, message1, parse_mode='html')
        bot.send_location(chat_id, NEWLOCATION['latitude'], NEWLOCATION['longitude'])
        photos = NEWLOCATION['photos']
        if len(photos) > 0:
            for photo in photos:
                bot.send_photo(chat_id, 'https://iuriid.github.io/img/ft-1.jpg')
        author = '<b>{}</b>'.format(from_user.first_name)
        comment = NEWLOCATION['comment']
        if comment != '':
            message2 = 'My new friend {} wrote:\n<i>{}</i>'.format(author, comment)
        else:
            message2 = 'I got acquainted with a new friend - {} :)'.format(author)
        bot.send_message(chat_id, message2, parse_mode='html')
        return True
    except Exception as e:
        print('new_location_summary() exception: {}'.format(e))
        return False

####################################### Functions END ####################################

'''
try:
    bot.polling(none_stop=True, timeout=1)
except Exception as e:
    print('Exception: {}'.format(e))
    time.sleep(15)
'''
####################################### TG BOT START ####################################
# Remove webhook, it fails sometimes the set if there is a previous webhook
bot.remove_webhook()

# Set webhook
bot.set_webhook(url=WEBHOOK_URL_BASE+WEBHOOK_URL_PATH,
                certificate=open(WEBHOOK_SSL_CERT, 'r'))
####################################### TG BOT END #######################################

app.run(host=WEBHOOK_LISTEN,
        port=WEBHOOK_PORT,
        ssl_context=(WEBHOOK_SSL_CERT, WEBHOOK_SSL_PRIV),
        debug=True)
'''
# Run Flask server
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
'''