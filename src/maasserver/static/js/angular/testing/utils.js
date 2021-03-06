/* Copyright 2015-2016 Canonical Ltd.  This software is licensed under the
 * GNU Affero General Public License version 3 (see the file LICENSE).
 *
 * Testing Utilities
 *
 * Helper functions that make testing easier.
 */

function makeString(size) {
    var chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" +
                "abcdefghijklmnopqrstuvwxyz" +
                "0123456789";
    if(!angular.isNumber(size)) {
        size = 10;
    }

    var i;
    var text = "";
    for(i = 0; i < size; i++) {
        text += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return text;
}

function makeName(name, size) {
    return name + "_" + makeString(size);
}

function makeFakeResponse(data, error) {
    if(error) {
        return angular.toJson({
            type: 1,
            rtype: 1,
            error: data
        });
    } else {
        return angular.toJson({
            type: 1,
            rtype: 0,
            result: data
        });
    }
}

function makeInteger(min, max) {
    return Math.floor(Math.random() * (max - min)) + min;
}

function pickItem(array) {
    return array[Math.floor(Math.random() * array.length)];
}
