from django import template
from django.utils.html import format_html

register = template.Library()

SIZES = {
    'sm': 'h-8 w-8 text-xs',
    'md': 'h-9 w-9 text-sm',
    'lg': 'h-12 w-12 text-base',
    'xl': 'h-20 w-20 text-xl',
}


@register.simple_tag
def avatar(photo=None, name='', size='md', extra_class=''):
    size_class = SIZES.get(size, SIZES['md'])
    classes = f'rounded-full object-cover shrink-0 {size_class} {extra_class}'.strip()
    if photo:
        try:
            url = photo.url
            return format_html(
                '<img src="{}" alt="{}" class="{}">',
                url,
                name or 'Photo',
                classes,
            )
        except (ValueError, AttributeError):
            pass
    initial = (name or '?')[0].upper()
    return format_html(
        '<span class="inline-flex items-center justify-center font-semibold '
        'bg-gradient-to-br from-blue-500 to-emerald-500 text-white {}">{}</span>',
        classes,
        initial,
    )
