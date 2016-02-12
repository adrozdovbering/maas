# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import (
    migrations,
    models,
)
import maasserver.models.cleansave


class Migration(migrations.Migration):

    dependencies = [
        ('maasserver', '0030_drop_all_old_funcs'),
    ]

    operations = [
        migrations.CreateModel(
            name='RegionRackRPCConnection',
            fields=[
                ('id', models.AutoField(serialize=False, auto_created=True, primary_key=True, verbose_name='ID')),
                ('created', models.DateTimeField(editable=False)),
                ('updated', models.DateTimeField(editable=False)),
                ('endpoint', models.ForeignKey(related_name='connections', to='maasserver.RegionControllerProcessEndpoint')),
                ('rack_controller', models.ForeignKey(related_name='connections', to='maasserver.RackController')),
            ],
            bases=(maasserver.models.cleansave.CleanSave, models.Model, object),
        ),
        migrations.AddField(
            model_name='node',
            name='managing_process',
            field=models.ForeignKey(editable=False, null=True, related_name='+', to='maasserver.RegionControllerProcess'),
        ),
        migrations.AlterUniqueTogether(
            name='regionrackrpcconnection',
            unique_together=set([('endpoint', 'rack_controller')]),
        ),
    ]