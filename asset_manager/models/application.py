from django.db import models
from django.utils.translation import gettext_lazy as _

from .base import BaseModel


class Application(BaseModel):
    class Language(models.TextChoices):
        PHP        = 'php',        _('PHP')
        RUBY       = 'ruby',       _('Ruby')
        PYTHON     = 'python',     _('Python')
        JAVASCRIPT = 'javascript', _('JavaScript')
        TYPESCRIPT = 'typescript', _('TypeScript')
        GO         = 'go',         _('Go')
        JAVA       = 'java',       _('Java')
        KOTLIN     = 'kotlin',     _('Kotlin')
        RUST       = 'rust',       _('Rust')
        OTHER      = 'other',      _('Other')

    class Framework(models.TextChoices):
        LARAVEL  = 'laravel',  _('Laravel')
        RAILS    = 'rails',    _('Ruby on Rails')
        DJANGO   = 'django',   _('Django')
        FASTAPI  = 'fastapi',  _('FastAPI')
        FLASK    = 'flask',    _('Flask')
        NEXTJS   = 'nextjs',   _('Next.js')
        NUXTJS   = 'nuxtjs',   _('Nuxt.js')
        REACT    = 'react',    _('React')
        VUE      = 'vue',      _('Vue.js')
        ANGULAR  = 'angular',  _('Angular')
        SPRING   = 'spring',   _('Spring Boot')
        EXPRESS  = 'express',  _('Express')
        NESTJS   = 'nestjs',   _('NestJS')
        NONE     = 'none',     _('None / Other')

    system = models.ForeignKey(
        'asset_manager.System',
        on_delete=models.CASCADE,
        related_name='applications',
        verbose_name=_("System"),
    )
    name           = models.CharField(max_length=255, verbose_name=_("App Name"))
    language       = models.CharField(max_length=20, choices=Language.choices, verbose_name=_("Language"))
    framework      = models.CharField(max_length=20, choices=Framework.choices, default=Framework.NONE, verbose_name=_("Framework"))
    repository_url = models.URLField(blank=True, null=True, verbose_name=_("Repository URL"))
    description    = models.TextField(blank=True, null=True, verbose_name=_("Description"))

    class Meta:
        verbose_name        = _("Application")
        verbose_name_plural = _("Applications")
        unique_together     = ('system', 'name')
        ordering            = ['system', 'name']

    def __str__(self):
        return f"{self.system.name} / {self.name}"


class AppEnvConfig(BaseModel):
    class Runtime(models.TextChoices):
        APACHE   = 'apache',   _('Apache')
        NGINX    = 'nginx',    _('Nginx')
        PUMA     = 'puma',     _('Puma')
        UNICORN  = 'unicorn',  _('Unicorn')
        UWSGI    = 'uwsgi',    _('uWSGI')
        GUNICORN = 'gunicorn', _('Gunicorn')
        NODE     = 'node',     _('Node.js')
        NONE     = 'none',     _('None / Other')

    class DeployMethod(models.TextChoices):
        EC2               = 'ec2',       _('EC2')
        ECS               = 'ecs',       _('ECS (Fargate/EC2)')
        LAMBDA            = 'lambda',    _('Lambda')
        K8S               = 'k8s',       _('Kubernetes')
        ELASTIC_BEANSTALK = 'beanstalk', _('Elastic Beanstalk')
        OTHER             = 'other',     _('Other')

    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name='env_configs',
        verbose_name=_("Application"),
    )
    environment = models.ForeignKey(
        'asset_manager.Environment',
        on_delete=models.CASCADE,
        related_name='app_configs',
        verbose_name=_("Environment"),
    )
    language_version  = models.CharField(max_length=50, verbose_name=_("Language Version"))
    framework_version = models.CharField(max_length=50, blank=True, null=True, verbose_name=_("Framework Version"))
    runtime           = models.CharField(max_length=20, choices=Runtime.choices, default=Runtime.NGINX, verbose_name=_("Runtime"))
    runtime_version   = models.CharField(max_length=50, blank=True, null=True, verbose_name=_("Runtime Version"))
    db_asset = models.ForeignKey(
        'asset_manager.Asset',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='app_db_configs',
        verbose_name=_("Database Asset"),
    )
    deploy_target = models.ForeignKey(
        'asset_manager.Asset',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='app_deploy_configs',
        verbose_name=_("Deploy Target"),
    )
    branch        = models.CharField(max_length=255, blank=True, null=True, verbose_name=_("Branch"))
    deploy_method = models.CharField(max_length=20, choices=DeployMethod.choices, default=DeployMethod.EC2, verbose_name=_("Deploy Method"))

    class Meta:
        verbose_name        = _("App Environment Config")
        verbose_name_plural = _("App Environment Configs")
        unique_together     = ('application', 'environment')
        ordering            = ['application', 'environment']

    def __str__(self):
        return f"{self.application.name} / {self.environment.name}"


class AppDependency(BaseModel):
    class DepType(models.TextChoices):
        LIBRARY    = 'library',    _('Library')
        MIDDLEWARE = 'middleware', _('Middleware')
        TOOL       = 'tool',       _('Tool')

    app_env_config = models.ForeignKey(
        AppEnvConfig,
        on_delete=models.CASCADE,
        related_name='dependencies',
        verbose_name=_("App Env Config"),
    )
    name     = models.CharField(max_length=255, verbose_name=_("Package Name"))
    version  = models.CharField(max_length=100, verbose_name=_("Version"))
    dep_type = models.CharField(max_length=20, choices=DepType.choices, default=DepType.LIBRARY, verbose_name=_("Type"))

    class Meta:
        verbose_name        = _("Dependency")
        verbose_name_plural = _("Dependencies")
        unique_together     = ('app_env_config', 'name')
        ordering            = ['dep_type', 'name']

    def __str__(self):
        return f"{self.name} {self.version}"
