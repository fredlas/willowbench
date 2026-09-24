#include <vector>
#include <iostream>
using namespace std;

// gives a list of ints in [101024, 998749] such that any pair differs in 2 or more digits

bool differ_in_two_digits(int n1, int n2)
{
  int ones1 = n1 %  10;
  int tens1 = n1 %  100;
  int huns1 = n1 %  1000;
  int thous1 = n1 % 10000;
  int mans1 = n1 %  100000;
  int dig11 = ones1;
  int dig21 = tens1 - ones1;
  int dig31 = huns1 - tens1;
  int dig41 = thous1 - huns1;
  int dig51 = mans1 - thous1;
  int dig61 = n1 - mans1;

  int ones2 = n2 %  10;
  int tens2 = n2 %  100;
  int huns2 = n2 %  1000;
  int thous2 = n2 % 10000;
  int mans2 = n2 %  100000;
  int dig12 = ones2;
  int dig22 = tens2 - ones2;
  int dig32 = huns2 - tens2;
  int dig42 = thous2 - huns2;
  int dig52 = mans2 - thous2;
  int dig62 = n2 - mans2;

  int diff = 0;
  if (dig11 != dig12) diff += 1;
  if (dig21 != dig22) diff += 1;
  if (dig31 != dig32) diff += 1;
  if (dig41 != dig42) diff += 1;
  if (dig51 != dig52) diff += 1;
  if (dig61 != dig62) diff += 1;

  return diff >= 2;
}

int main()
{
  vector<int> nums;
  for (int i=101024; i<=998749; i++)
    nums.push_back(i);

  vector<int> res;
  for (int i=0; i<nums.size(); i++)
  {
    int n = nums[i];
    if (n % 1000 == 0)
      cerr<<n<<endl;
    bool add = true;
    for (int e : res)
    {
      if (!differ_in_two_digits(n, e))
      {
        add = false;
        break;
      }
    }
    if (add)
    {
      res.push_back(n);
      int ones = n % 10;
      i += 9 - ones;
    }
  }
  for (int r : res)
    cout << r << "\n";
}
