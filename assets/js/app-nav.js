(function (global) {
  'use strict';

  var _currentGroup = 'holder';

  function getCurrentGroup() {
    return _currentGroup;
  }

  function setCurrentGroup(groupName) {
    _currentGroup = groupName || 'holder';
    return _currentGroup;
  }

  global.AppNav = {
    getCurrentGroup: getCurrentGroup,
    setCurrentGroup: setCurrentGroup,
  };
})(window);