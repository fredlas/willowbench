import stripe
import os
from flask import redirect, url_for, flash

import forms
import our_mongo as mdb
import utils
from utils import log_error,log_warn,log_info,log_debug # pylint: disable=unused-import

from dotenv import load_dotenv
load_dotenv("/home/admin/willow_fe_env")
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET')

CENTS_PER_CREDIT = 2

if not stripe.api_key:
    utils.crash("STRIPE_SECRET_KEY environment variable not set!")

if not webhook_secret:
    utils.crash("STRIPE_WEBHOOK_SECRET environment variable not set!")

# Validate that keys look reasonable
if not stripe.api_key.startswith('sk_'):
    utils.crash("STRIPE_SECRET_KEY doesn't look like a valid Stripe key (should start with sk_)")

if len(webhook_secret) < 10:
    utils.crash("STRIPE_WEBHOOK_SECRET appears too short to be valid")

def get_or_create_stripe_customer_id(willow_customer_id):
    """
    Retrieves the Stripe Customer ID for a given Willowbench customer ID.
    If it doesn't exist, creates a new Stripe Customer and stores the ID.
    """
    customer_details = mdb.get_customer_details(willow_customer_id)
    if not customer_details:
        log_error(f"Customer details not found for Willow customer_id {willow_customer_id}")
        return None

    stripe_customer_id = customer_details.get('stripe_customer_id')
    if not stripe_customer_id:
        try:
            customer = stripe.Customer.create(
                idempotency_key=willow_customer_id,
                metadata={
                    'willowbench_customer_id': willow_customer_id,
                    'willowbench_admin_email': customer_details.get('admin_email')
                },
                description=f"Willowbench Customer ID: {willow_customer_id}"
            )
            stripe_customer_id = customer.id
            mdb.update_customer_stripe_id(willow_customer_id, stripe_customer_id)
            log_info(f"Created new Stripe Customer {stripe_customer_id} for Willowbench customer {willow_customer_id}")
        except stripe.error.StripeError as e:
            log_error(f"Stripe API error creating customer: {e}")
            flash('Stripe failed to create a Customer identity for you. Maybe try again?', 'warning')
            return None
        except Exception as e:
            log_error(f"An unexpected error occurred creating Stripe customer: {e}")
            flash('Stripe failed to create a Customer identity for you. Maybe try again?', 'warning')
            return None
    return stripe_customer_id

def handle_update_subscription_request(username):
    """
    Handles the request to update subscription billing info.
    Checks admin status, gets customer ID, and initiates Stripe Customer Portal session.
    """
    user = mdb.find_user(username)
    if not user:
        flash('Username not found.', 'error')
        return redirect(url_for('login'))
    if not user.get('is_admin'):
        flash('Your account is not the admin for your customer.', 'warning')
        return redirect(url_for('index'))

    willow_customer_id = user.get('customer_id')
    if not willow_customer_id:
        flash('Could not determine your customer ID.', 'error')
        return redirect(url_for('index'))

    stripe_customer_id = get_or_create_stripe_customer_id(willow_customer_id)
    if not stripe_customer_id:
        log_error(f"Could not get or create Stripe Customer ID for Willow customer_id {willow_customer_id}")
        flash('Error initiating subscription update. Please try again.', 'warning')
        return redirect(url_for('admin_console'))

    try:
        # Create a Stripe Customer Portal session
        session = stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url='https://bench.willowbench.bio/admin',
        )
        return redirect(session.url) # Return the URL to redirect the user to
    except stripe.error.StripeError as e:
        log_error(f"Stripe API error creating portal session: {e}")
        flash('Error initiating subscription update. Please try again.', 'warning')
        return redirect(url_for('admin_console'))
    except Exception as e:
        log_error(f"An unexpected error occurred creating portal session: {e}")
        flash('Error initiating subscription update. Please try again.', 'warning')
        return redirect(url_for('admin_console'))


def handle_purchase_credits_request(username):
    """
    Handles the request to purchase credits.
    Checks admin status, gets customer ID, validates form, and initiates Stripe Checkout session.
    """
    user = mdb.find_user(username)
    if not user:
        flash('Username not found.', 'error')
        return redirect(url_for('login'))
    if not user.get('is_admin'):
        flash('Your account is not the admin for your customer.', 'warning')
        return redirect(url_for('index'))

    willow_customer_id = user.get('customer_id')
    if not willow_customer_id:
        flash('Could not determine customer ID.', 'error')
        return redirect(url_for('index'))

    form = forms.PurchaseCreditsForm()
    if form.validate_on_submit():
        price_id = 'price_1RQg8kQncGmaW6uLqmVjZoYI' # TODO probably need to update this when moving to production Stripe

        stripe_customer_id = get_or_create_stripe_customer_id(willow_customer_id)
        if not stripe_customer_id:
            log_error(f"Could not get or create Stripe Customer ID for Willow customer_id {willow_customer_id}")
            flash('Error initiating credit purchase: Could not get your Stripe Customer ID.', 'warning')
            return redirect(url_for('admin_console'))

        try:
            # Create a Stripe Checkout session for a one-time payment
            session = stripe.checkout.Session.create(
                customer=stripe_customer_id,
                line_items=[{ 'price': price_id,
                              'quantity': form.amount.data }],
                mode='payment', # Use payment mode for one-time purchases
                # NOTE yes, this is supposed to be non-f {CHECKOUT_SESSION_ID}; Stripe fills it on their end.
                success_url='https://bench.willowbench.bio/billing/success?session_id={CHECKOUT_SESSION_ID}',
                cancel_url='https://bench.willowbench.bio/billing/cancel',
                # Pass our internal customer ID as client_reference_id
                client_reference_id=str(willow_customer_id),
                metadata={
                    'willowbench_customer_id': str(willow_customer_id),
                    'payment_type': 'credits_purchase' # Custom metadata to identify the purpose
                }
            )
            return redirect(session.url) # Return the URL to redirect the user to
        except stripe.error.StripeError as e:
            log_error(f"Stripe API error creating checkout session: {e}")
            flash('Stripe gave us an error when initiating credit purchase. Please try again.', 'warning')
            return redirect(url_for('admin_console'))
        except Exception as e:
            log_error(f"An unexpected error occurred creating checkout session: {e}")
            flash('Stripe gave us an error when initiating credit purchase. Please try again.', 'warning')
            return redirect(url_for('admin_console'))

    flash('Invalid selection for credit purchase.', 'warning')
    return redirect(url_for('admin_console'))


# TODO need to periodically clean this
webhooks_already_received = {} # idempotency guard
def handle_webhook_completed(event_data_object):
    session = event_data_object # Contains the checkout session details
    log_info(f"Webhook: checkout.session.completed event received for session {session.id}")
    if session.id in webhooks_already_received:
        log_warn(f"oh, actually, {session.id} was a DUPLICATE; nevermind.")
        return 'Success', 200
    webhooks_already_received[session.id] = True

    # Check if this session was for a credits purchase
    payment_type_metadata = session.get('metadata', {}).get('payment_type')
    if payment_type_metadata != 'credits_purchase':
        log_error(f"Webhook Error: payment_type metadata expected credits_purchase, got {payment_type_metadata}.")
        return 'unexpected payment_type metadata', 400

    willow_customer_id_str = session.get('client_reference_id')
    amount_total_cents = session.get('amount_total') # Amount in cents

    if willow_customer_id_str and amount_total_cents is not None:
        try:
            willow_customer_id = int(willow_customer_id_str)
            credits_to_add = amount_total_cents // CENTS_PER_CREDIT
            log_info(f"Processing credits purchase for Willow customer_id {willow_customer_id}: {amount_total_cents} cents -> {credits_to_add} credits")

            mdb.add_credits(willow_customer_id, credits_to_add)
            log_info(f"Successfully added {credits_to_add} credits to Willow customer_id {willow_customer_id}")
            return 'Success', 200

        except ValueError:
            log_error(f"Webhook Error: Invalid Willow customer_id in client_reference_id: {willow_customer_id_str}")
            return 'bad willow customer_id', 400
        except Exception as e:
            log_error(f"Webhook Error: Failed to add credits for Willow customer_id {willow_customer_id_str}: {e}")
            return 'unknown error', 500
    else:
        log_error("Webhook Error: Missing client_reference_id or amount_total in checkout.session.completed event metadata/object.")
        return 'bad Stripe params', 400

def handle_stripe_webhook(payload, signature):
    """
    Handles incoming Stripe webhook events.
    Verifies the signature and processes relevant events.
    """
    event = None
    if not webhook_secret:
        log_error("Webhook Error: STRIPE_WEBHOOK_SECRET environment variable not set!")
        return 'Webhook secret not configured', 500

    try:
        # Verify webhook signature using webhook_secret
        event = stripe.Webhook.construct_event(payload, signature, webhook_secret)
    except ValueError as e:
        # Invalid payload
        log_error(f"Webhook Error: Invalid payload - {e}")
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        log_error(f"Webhook Error: Invalid signature - {e}")
        return 'Invalid signature', 400
    except Exception as e:
        log_error(f"Webhook Error: Unexpected error during signature verification - {e}")
        return 'Internal server error', 500

    # Handle the event
    if event['type'] == 'checkout.session.completed':
        return handle_webhook_completed(event['data']['object'])

    elif event['type'] == 'customer.subscription.updated':
        subscription = event['data']['object']
        log_info(f"Webhook: customer.subscription.updated event received for subscription {subscription.id}")
        # TODO You could update subscription status in your DB here if needed

    elif event['type'] == 'customer.subscription.deleted':
        subscription = event['data']['object']
        log_info(f"Webhook: customer.subscription.deleted event received for subscription {subscription.id}")
        # TODO You could mark the customer's subscription as inactive in your DB here

    else:
        event_type = event['type']
        log_info(f"got unhandled Stripe webhook event {event_type}. Not necessarily a bug, just letting you know.")

    # Return a 200 response to acknowledge receipt of the event
    return 'Success', 200
