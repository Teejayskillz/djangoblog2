from django import forms
from .models import Comment
from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
import re


COMMENT_MAX_LENGTH = 2000
COMMENT_MIN_LENGTH = 3


class CommentForm(forms.ModelForm):
    """
    Comment form for logged-in users.

    Only the comment body is user-editable: the author (name/email) comes from
    the authenticated account, never from the browser. The `website` field is a
    honeypot - it is hidden with CSS and real users never fill it in.
    """

    MAX_LENGTH = COMMENT_MAX_LENGTH
    MIN_LENGTH = COMMENT_MIN_LENGTH

    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'comment-honeypot',
            'tabindex': '-1',
            'autocomplete': 'off',
            'aria-hidden': 'true',
        }),
        label='',
    )

    class Meta:
        model = Comment
        fields = ['comment']
        error_messages = {
            'comment': {'required': 'Please write a comment before submitting.'},
        }
        widgets = {
            'comment': forms.Textarea(attrs={
                'rows': 4,
                'maxlength': COMMENT_MAX_LENGTH,
                'placeholder': 'Share your thoughts...',
                'class': 'form-control',
                'required': 'required',
            }),
        }

    def clean_comment(self):
        # Normalise whitespace before validating, so "   " is an empty comment
        # and pasted blocks of blank lines are collapsed.
        raw = self.cleaned_data.get('comment') or ''
        raw = raw.replace('\r\n', '\n').replace('\r', '\n')
        lines = [line.rstrip() for line in raw.split('\n')]
        text = '\n'.join(lines)
        text = re.sub(r'\n{3,}', '\n\n', text).strip()

        if len(text) < self.MIN_LENGTH:
            raise ValidationError('Please write a comment before submitting.')

        if len(text) > self.MAX_LENGTH:
            raise ValidationError(
                f'Comments cannot be longer than {self.MAX_LENGTH} characters.'
            )

        return text

    def clean_website(self):
        # Honeypot: a value here means a bot filled the hidden field.
        if self.cleaned_data.get('website'):
            raise ValidationError('Your comment could not be submitted.')
        return ''


class PasswordForm(forms.Form):
    password = forms.CharField(label="Enter Password", widget=forms.PasswordInput)        


class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your email address'
        })
    )
    username = forms.CharField(
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Choose a username'
        })
    )
    password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter password'
        })
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Confirm password'
        })
    )
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']
    
    def clean_username(self):
        username = self.cleaned_data.get('username')
        if not re.match(r'^[a-zA-Z0-9_]+$', username):
            raise ValidationError('Username can only contain letters, numbers, and underscores.')
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError('This username is already taken.')
        return username
    
    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('This email is already registered.')
        return email
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add Bootstrap classes to all fields
        for field_name, field in self.fields.items():
            if field.widget.attrs.get('class'):
                field.widget.attrs['class'] += ' form-control'
            else:
                field.widget.attrs['class'] = 'form-control'    