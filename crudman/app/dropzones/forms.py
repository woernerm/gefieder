from django import forms
from django.utils import timezone


class MultipleFileInput(forms.ClearableFileInput):
    # Opting in to multiple selection this way is the documented Django pattern
    # ("Uploading multiple files"); the widget then renders the multiple attribute and
    # hands the form a list of files.
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

    ALWAYS = "always"
    UNTIL_REPLACED = "until_replaced"
    PERIOD = "period"
    VALIDITY_CHOICES = [
        (UNTIL_REPLACED, "Valid until replaced by a later upload"),
        (ALWAYS, "Always valid"),
        (PERIOD, "Valid for a fixed period"),
    ]

    files = MultipleFileField(label="Files")
    validity = forms.ChoiceField(
        choices=VALIDITY_CHOICES,
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
        # Preselect matching files in the browser's file dialog when the dropzone
        # declares an accept list like ".csv,.xlsx". A prose format ("Excel files") is
        # not a valid accept value and would filter everything out, so it is skipped.
        if dropzone and dropzone.file_format:
            tokens = [t.strip() for t in dropzone.file_format.split(",") if t.strip()]
            if tokens and all(t.startswith(".") or "/" in t for t in tokens):
                self.fields["files"].widget.attrs["accept"] = ",".join(tokens)

    def clean(self):
        """Map the validity selection onto the (valid_from, valid_until) pair the
        Upload model stores; see the model for the NULL semantics."""
        cleaned = super().clean()
        mode = cleaned.get("validity")
        if mode == self.ALWAYS:
            cleaned["valid_from"] = None
            cleaned["valid_until"] = None
        elif mode == self.UNTIL_REPLACED:
            # An open end; the start defaults to "now" so a later upload clips it here.
            cleaned["valid_from"] = cleaned.get("valid_from") or timezone.now()
            cleaned["valid_until"] = None
        elif mode == self.PERIOD:
            start, end = cleaned.get("valid_from"), cleaned.get("valid_until")
            if not start or not end:
                raise forms.ValidationError(
                    "A fixed period needs both a start and an end date."
                )
            if end <= start:
                raise forms.ValidationError(
                    "The end of the validity period must be after its start."
                )
        return cleaned
