from django import forms
from .models import PaymentTransaction


class ManualPaymentForm(forms.ModelForm):
    class Meta:
        model = PaymentTransaction
        fields = ['proof_of_payment', 'user_notes']
        widgets = {
            'proof_of_payment': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': 'image/*,.pdf',
                'required': 'required',
            }),
            'user_notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Enter sender account name, bank name, transaction ID, or transfer remark...'
            })
        }
        labels = {
            'proof_of_payment': 'Upload Bank Transfer Receipt / Screenshot',
            'user_notes': 'Depositor Name / Bank Transfer Notes',
        }

    def clean_proof_of_payment(self):
        proof = self.cleaned_data.get('proof_of_payment')
        if not proof:
            raise forms.ValidationError("Please upload your bank transfer receipt or screenshot to proceed.")
        return proof

