import flask_wtf
from wtforms import StringField, PasswordField, SubmitField, HiddenField, IntegerField, BooleanField, FieldList, FormField
from wtforms.validators import DataRequired, Length, Email, Optional

def generate_form_class(inputs):
  fields = {}
  for the_input in inputs:
    is_optional = the_input.get('optional', False)
    default_val = the_input.get('default')
    the_input_type = the_input['type']

    if the_input_type == 'File':
      field_type = StringField
    elif the_input_type in ('String', 'Struct') or 'Array' in the_input_type:
      field_type = StringField
    elif the_input_type == 'Int':
      field_type = StringField
    elif the_input_type == 'Float':
      field_type = StringField
    elif the_input_type == 'Boolean':
      field_type = BooleanField
    else:
      raise ValueError('unknown input type: ' + the_input_type)

    if the_input_type == 'Boolean':
      validators = []
    elif not is_optional:
      validators = [DataRequired()]
    else:
      validators = [Optional()]

    field_kwargs = {'validators': validators}
    if default_val is not None:
      field_kwargs['default'] = default_val

    # Add the unbound field to our dictionary of fields
    fields[the_input['name']] = field_type(the_input['name'], **field_kwargs)

  # Create the class dynamically using type()
  return type('DynamicForm', (flask_wtf.FlaskForm,), fields)

class LoginForm(flask_wtf.FlaskForm):
  useremail = StringField('Email', validators=[DataRequired(), Length(min=5, max=333)])
  password = PasswordField('Password', validators=[DataRequired()])
  submit = SubmitField('Login')

class ResetPasswordForm(flask_wtf.FlaskForm):
  useremail = StringField('Email', validators=[DataRequired(), Length(min=5, max=333)])
  submit = SubmitField('Reset Password')

class SetPasswordForm(flask_wtf.FlaskForm):
  hidden_code = HiddenField()
  password = PasswordField('Password', validators=[DataRequired()])
  submit = SubmitField('Set Password')

class JobCancelForm(flask_wtf.FlaskForm):
  submit = SubmitField('Cancel Job')

class JustCSRFForm(flask_wtf.FlaskForm):
  pass

class EmailFieldForm(flask_wtf.FlaskForm):
  email = StringField(validators=[Optional(), Email()])

class InviteUsersForm(flask_wtf.FlaskForm):
  emails = FieldList(FormField(EmailFieldForm), min_entries=1)
  submit = SubmitField('Send Invitations')

class PurchaseCreditsForm(flask_wtf.FlaskForm):
  # NOTE this is the way to do it with discrete package sizes, for bulk discounts. not doing that for now.
  # Create choices from the credit_price_map in config.global_config
  # The value will be the Stripe Price ID, the label will be the credit amount
  # choices = [(price_id, f'{amount} Credits')
  #      for amount, price_id in config.global_config.get('credit_price_map', {}).items()]
  # amount = SelectField('Select Credit Package', choices=choices, validators=[DataRequired()])
  amount = IntegerField('Credits to Buy')
  submit = SubmitField('Purchase Credits')
