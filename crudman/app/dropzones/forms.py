from django import forms
from django.utils import timezone

from .models import Dropzone


class MultipleFileInput(forms.ClearableFileInput):
    # The documented Django pattern ("Uploading multiple files"): the widget renders the
    # multiple attribute and hands the form a list of files.
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """A FileField whose cleaned value is the list of all selected files."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_file_clean(entry, initial) for entry in data]
        return [single_file_clean(data, initial)]


class UploadForm(forms.Form):
    """The upload form: the files plus one validity period for the whole set."""

    # The modes live on the model, being also a dropzone's default validity; the aliases
    # keep the form self-describing.
    ALWAYS = Dropzone.Validity.ALWAYS
    UNTIL_REPLACED = Dropzone.Validity.UNTIL_REPLACED
    PERIOD = Dropzone.Validity.PERIOD

    files = MultipleFileField(label="Files")
    validity = forms.ChoiceField(
        choices=Dropzone.Validity.choices,
        initial=UNTIL_REPLACED,
        widget=forms.RadioSelect,
        label="Validity of the files",
    )
    valid_from = forms.DateTimeField(
        required=False,
        label="Valid from",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    valid_until = forms.DateTimeField(
        required=False,
        label="Valid until",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    def __init__(self, *args, dropzone=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Preselected only; the uploader may choose another mode.
        if dropzone:
            self.fields["validity"].initial = dropzone.default_validity
        # Preselect matching files in the browser's file dialog. A prose format ("Excel
        # files") is not a valid accept value and would filter everything out.
        if dropzone and dropzone.file_format:
            tokens = [t.strip() for t in dropzone.file_format.split(",") if t.strip()]
            if tokens and all(t.startswith(".") or "/" in t for t in tokens):
                self.fields["files"].widget.attrs["accept"] = ",".join(tokens)

    def clean(self):
        """Map the validity selection onto the (valid_from, valid_until) pair.

        Returns:
            The cleaned data, its date fields set as the Upload model stores them; see
            that model for the NULL semantics.

        Raises:
            ValidationError: The period's end is not after its start.
        """
        cleaned = super().clean()
        mode = cleaned.get("validity")
        if mode == self.ALWAYS:
            cleaned["valid_from"] = None
            cleaned["valid_until"] = None
        elif mode == self.UNTIL_REPLACED:
            # "From now on", so a later upload clips it here. The date fields are hidden
            # for this mode, and a stray submitted value is ignored.
            cleaned["valid_from"] = timezone.now()
            cleaned["valid_until"] = None
        elif mode == self.PERIOD:
            # An empty start means "now"; an empty end stays open, so a later upload
            # still clips it.
            start = cleaned.get("valid_from") or timezone.now()
            end = cleaned.get("valid_until")
            if end and end <= start:
                raise forms.ValidationError(
                    "The end of the validity period must be after its start."
                )
            cleaned["valid_from"] = start
            cleaned["valid_until"] = end
        return cleaned
