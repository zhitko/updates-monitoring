# Generated by Django 5.1.3 on 2024-11-29 13:25

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_alter_provider_provider_type'),
    ]

    operations = [
        migrations.RenameField(
            model_name='host',
            old_name='is_arhived',
            new_name='is_archived',
        ),
    ]
