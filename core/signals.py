from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import AuditLog, Document


@receiver(pre_save, sender=Document)
def remember_if_adding(sender, instance, **kwargs):
    # Django flips _state.adding to False before post_save fires, so capture it here.
    instance._was_adding = instance._state.adding


@receiver(post_save, sender=Document)
def log_document_save(sender, instance, **kwargs):
    AuditLog.objects.create(
        actor=instance.created_by,
        action=AuditLog.Action.CREATED if instance._was_adding else AuditLog.Action.UPDATED,
        model_name='Document',
        object_id=instance.id,
    )
