from django.db import migrations, models


def backfill(apps, schema_editor):
    Round = apps.get_model('tournaments', 'Round')
    Pairing = apps.get_model('tournaments', 'Pairing')

    # every pre-existing round was started at creation time under the old single-step flow
    Round.objects.update(is_started=True)
    for round_obj in Round.objects.all().iterator():
        Round.objects.filter(pk=round_obj.pk).update(started_at=round_obj.created_at)
        board = 0
        for pairing in Pairing.objects.filter(round=round_obj).order_by('id'):
            if pairing.bye_player_id:
                continue
            board += 1
            Pairing.objects.filter(pk=pairing.pk).update(board_number=board)


def unbackfill(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('tournaments', '0005_round_completed_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='round',
            name='is_started',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='round',
            name='started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='pairing',
            name='board_number',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterModelOptions(
            name='pairing',
            options={'ordering': ['round__round_number', 'board_number', 'id']},
        ),
        migrations.RunPython(backfill, unbackfill),
    ]
