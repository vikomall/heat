# vim: tabstop=4 shiftwidth=4 softtabstop=4

#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import collections
import numbers
import re


class InvalidSchemaError(Exception):
    pass


class Schema(collections.Mapping):
    """
    Schema base class for validating properties or parameters.

    Schema objects are serialisable to dictionaries following a superset of
    the HOT input Parameter schema using dict().

    Serialises to JSON in the form::

        {
            'type': 'list',
            'required': False
            'constraints': [
                {
                    'length': {'min': 1},
                    'description': 'List must not be empty'
                }
            ],
            'schema': {
                '*': {
                    'type': 'string'
                }
            },
            'description': 'An example list property.'
        }
    """

    KEYS = (
        TYPE, DESCRIPTION, DEFAULT, SCHEMA, REQUIRED, CONSTRAINTS,
    ) = (
        'type', 'description', 'default', 'schema', 'required', 'constraints',
    )

    TYPES = (
        INTEGER,
        STRING, NUMBER, BOOLEAN,
        MAP, LIST
    ) = (
        'Integer',
        'String', 'Number', 'Boolean',
        'Map', 'List'
    )

    def __init__(self, data_type, description=None,
                 default=None, schema=None,
                 required=False, constraints=[]):
        self._len = None
        self.type = data_type
        if self.type not in Schema.TYPES:
            raise InvalidSchemaError(_('Invalid type (%s)') % self.type)

        self.description = description
        self.required = required

        if isinstance(schema, type(self)):
            if self.type != Schema.LIST:
                msg = _('Single schema valid only for '
                        '%(ltype)s, not %(utype)s') % dict(ltype=Schema.LIST,
                                                           utype=self.type)
                raise InvalidSchemaError(msg)

            self.schema = AnyIndexDict(schema)
        else:
            self.schema = schema
        if self.schema is not None and self.type not in (Schema.LIST,
                                                         Schema.MAP):
            msg = _('Schema valid only for %(ltype)s or '
                    '%(mtype)s, not %(utype)s') % dict(ltype=Schema.LIST,
                                                       mtype=Schema.MAP,
                                                       utype=self.type)
            raise InvalidSchemaError(msg)

        self.constraints = constraints
        for c in constraints:
            if self.type not in c.valid_types:
                err_msg = _('%(name)s constraint '
                            'invalid for %(utype)s') % dict(
                                name=type(c).__name__,
                                utype=self.type)
                raise InvalidSchemaError(err_msg)

        self.default = default
        if self.default is not None:
            try:
                self.validate_constraints(self.default)
            except (ValueError, TypeError) as exc:
                raise InvalidSchemaError(_('Invalid default '
                                           '%(default)s (%(exc)s)') %
                                         dict(default=self.default, exc=exc))

    @staticmethod
    def str_to_num(value):
        """Convert a string representation of a number into a numeric type."""
        if isinstance(value, numbers.Number):
            return value
        try:
            return int(value)
        except ValueError:
            return float(value)

    def validate_constraints(self, value):
        for constraint in self.constraints:
            constraint.validate(value)

    def __getitem__(self, key):
        if key == self.TYPE:
            return self.type.lower()
        elif key == self.DESCRIPTION:
            if self.description is not None:
                return self.description
        elif key == self.DEFAULT:
            if self.default is not None:
                return self.default
        elif key == self.SCHEMA:
            if self.schema is not None:
                return dict((n, dict(s)) for n, s in self.schema.items())
        elif key == self.REQUIRED:
            return self.required
        elif key == self.CONSTRAINTS:
            if self.constraints:
                return [dict(c) for c in self.constraints]

        raise KeyError(key)

    def __iter__(self):
        for k in self.KEYS:
            try:
                self[k]
            except KeyError:
                pass
            else:
                yield k

    def __len__(self):
        if self._len is None:
            self._len = len(list(iter(self)))
        return self._len


class AnyIndexDict(collections.Mapping):
    """
    A Mapping that returns the same value for any integer index.

    Used for storing the schema for a list. When converted to a dictionary,
    it contains a single item with the key '*'.
    """

    ANYTHING = '*'

    def __init__(self, value):
        self.value = value

    def __getitem__(self, key):
        if key != self.ANYTHING and not isinstance(key, (int, long)):
            raise KeyError(_('Invalid key %s') % str(key))

        return self.value

    def __iter__(self):
        yield self.ANYTHING

    def __len__(self):
        return 1


class Constraint(collections.Mapping):
    """
    Parent class for constraints on allowable values for a Property.

    Constraints are serialisable to dictionaries following the HOT input
    Parameter constraints schema using dict().
    """

    (DESCRIPTION,) = ('description',)

    def __init__(self, description=None):
        self.description = description

    def __str__(self):
        def desc():
            if self.description:
                yield self.description
            yield self._str()

        return '\n'.join(desc())

    def validate(self, value):
        if not self._is_valid(value):
            if self.description:
                err_msg = self.description
            else:
                err_msg = self._err_msg(value)
            raise ValueError(err_msg)

    @classmethod
    def _name(cls):
        return '_'.join(w.lower() for w in re.findall('[A-Z]?[a-z]+',
                                                      cls.__name__))

    def __getitem__(self, key):
        if key == self.DESCRIPTION:
            if self.description is None:
                raise KeyError(key)
            return self.description

        if key == self._name():
            return self._constraint()

        raise KeyError(key)

    def __iter__(self):
        if self.description is not None:
            yield self.DESCRIPTION

        yield self._name()

    def __len__(self):
        return 2 if self.description is not None else 1


class Range(Constraint):
    """
    Constrain values within a range.

    Serialises to JSON as::

        {
            'range': {'min': <min>, 'max': <max>},
            'description': <description>
        }
    """

    (MIN, MAX) = ('min', 'max')

    valid_types = (Schema.INTEGER, Schema.NUMBER)

    def __init__(self, min=None, max=None, description=None):
        super(Range, self).__init__(description)
        self.min = min
        self.max = max

        for param in (min, max):
            if not isinstance(param, (float, int, long, type(None))):
                raise InvalidSchemaError(_('min/max must be numeric'))

        if min is max is None:
            raise InvalidSchemaError(_('range must have min and/or max'))

    def _str(self):
        if self.max is None:
            fmt = _('The value must be at least %(min)s.')
        elif self.min is None:
            fmt = _('The value must be no greater than %(max)s.')
        else:
            fmt = _('The value must be in the range %(min)s to %(max)s.')
        return fmt % self._constraint()

    def _err_msg(self, value):
        return '%s is out of range (min: %s, max: %s)' % (value,
                                                          self.min,
                                                          self.max)

    def _is_valid(self, value):
        value = Schema.str_to_num(value)

        if self.min is not None:
            if value < self.min:
                return False

        if self.max is not None:
            if value > self.max:
                return False

        return True

    def _constraint(self):
        def constraints():
            if self.min is not None:
                yield self.MIN, self.min
            if self.max is not None:
                yield self.MAX, self.max

        return dict(constraints())


class Length(Range):
    """
    Constrain the length of values within a range.

    Serialises to JSON as::

        {
            'length': {'min': <min>, 'max': <max>},
            'description': <description>
        }
    """

    valid_types = (Schema.STRING, Schema.LIST)

    def __init__(self, min=None, max=None, description=None):
        super(Length, self).__init__(min, max, description)

        for param in (min, max):
            if not isinstance(param, (int, long, type(None))):
                msg = _('min/max length must be integral')
                raise InvalidSchemaError(msg)

    def _str(self):
        if self.max is None:
            fmt = _('The length must be at least %(min)s.')
        elif self.min is None:
            fmt = _('The length must be no greater than %(max)s.')
        else:
            fmt = _('The length must be in the range %(min)s to %(max)s.')
        return fmt % self._constraint()

    def _err_msg(self, value):
        return 'length (%d) is out of range (min: %s, max: %s)' % (len(value),
                                                                   self.min,
                                                                   self.max)

    def _is_valid(self, value):
        return super(Length, self)._is_valid(len(value))


class AllowedValues(Constraint):
    """
    Constrain values to a predefined set.

    Serialises to JSON as::

        {
            'allowed_values': [<allowed1>, <allowed2>, ...],
            'description': <description>
        }
    """

    valid_types = (Schema.STRING, Schema.INTEGER, Schema.NUMBER,
                   Schema.BOOLEAN)

    def __init__(self, allowed, description=None):
        super(AllowedValues, self).__init__(description)
        if (not isinstance(allowed, collections.Sequence) or
                isinstance(allowed, basestring)):
            raise InvalidSchemaError(_('AllowedValues must be a list'))
        self.allowed = tuple(allowed)

    def _str(self):
        allowed = ', '.join(str(a) for a in self.allowed)
        return _('Allowed values: %s') % allowed

    def _err_msg(self, value):
        allowed = '[%s]' % ', '.join(str(a) for a in self.allowed)
        return '"%s" is not an allowed value %s' % (value, allowed)

    def _is_valid(self, value):
        return value in self.allowed

    def _constraint(self):
        return list(self.allowed)


class AllowedPattern(Constraint):
    """
    Constrain values to a predefined regular expression pattern.

    Serialises to JSON as::

        {
            'allowed_pattern': <pattern>,
            'description': <description>
        }
    """

    valid_types = (Schema.STRING,)

    def __init__(self, pattern, description=None):
        super(AllowedPattern, self).__init__(description)
        self.pattern = pattern
        self.match = re.compile(pattern).match

    def _str(self):
        return _('Value must match pattern: %s') % self.pattern

    def _err_msg(self, value):
        return '"%s" does not match pattern "%s"' % (value, self.pattern)

    def _is_valid(self, value):
        match = self.match(value)
        return match is not None and match.end() == len(value)

    def _constraint(self):
        return self.pattern
